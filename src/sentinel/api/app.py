"""Sentinel API (TDD §12): the investigator workbench's only door into the system.

Reads come from Postgres (case records) and the LangGraph checkpointer (case state). Every change
goes out as a Kafka message that the workers apply, exactly as in the Kafka flow: the API checks
roles and business rules first so a bad request is refused here, not ignored later by a worker.

Run locally with `sentinel api` (http://localhost:8000/docs).
"""

import json
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sse_starlette.sse import EventSourceResponse

from sentinel.api.auth import Principal, current_user, require
from sentinel.api.backend import Backend, live_backend
from sentinel.api.models import ApprovalIn, DecisionIn, QALabel, ReplyIn
from sentinel.events import (ALERTS_TOPIC, DECISIONS_TOPIC, FOLLOWUPS_TOPIC, AlertEvent, ApprovalEvent,
                             DecisionEvent, FollowUpEvent)
from sentinel.graph import run_config
from sentinel.guardrails.pii import PiiVault
from sentinel.hitl import recommendation_of, validate_approval, validate_decision
from sentinel.persistence import STATUS_AFTER_DECISION

INVESTIGATORS = ("l1", "l2")
# Parts of the case state the workbench shows; pii_vault (the token -> value map) never leaves the API
VIEW_KEYS = ("tier", "narrative", "findings", "evidence", "qa_issues", "qa_rounds", "info_request", "follow_up",
             "decision", "security_events", "usage", "versions", "budget_exceeded")

REQUESTS = Counter("sentinel_api_requests_total", "API requests", ["method", "route", "status"])
LATENCY = Histogram("sentinel_api_request_seconds", "API request latency", ["method", "route"])
SUBMITTED = Counter("sentinel_api_submissions_total", "Messages published by the API", ["kind"])


def create_app(backend: Backend | None = None) -> FastAPI:
    """`backend` is injected by tests; otherwise the app connects to Postgres and Kafka on startup."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if backend is not None:
            app.state.backend = backend
            yield
            return
        async with live_backend() as live:
            app.state.backend = live
            yield

    app = FastAPI(title="Sentinel API", version="0.7.0", lifespan=lifespan,
                  description="AML investigation cases: queue, review packets, decisions, approvals, "
                              "customer replies, live progress and QA labels.")

    @app.middleware("http")
    async def metrics(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        REQUESTS.labels(request.method, route, response.status_code).inc()
        LATENCY.labels(request.method, route).observe(time.perf_counter() - start)
        return response

    def be(request: Request) -> Backend:
        return request.app.state.backend

    async def record_of(b: Backend, case_id: str) -> dict:
        row = await b.cases.get(case_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown case {case_id}")
        return row

    def expect_status(row: dict, *allowed: str) -> None:
        if row["status"] not in allowed:  # BR-10 and its equivalents for approvals and replies
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"case {row['case_id']} is {row['status']}, not {' or '.join(allowed)}")

    async def publish(b: Backend, topic: str, event, kind: str) -> dict:
        await b.send(topic, event)
        SUBMITTED.labels(kind).inc()
        return {"accepted": True, "case_id": event.case_id, "kind": kind}

    # --- Ops -----------------------------------------------------------------------------------
    @app.get("/healthz", tags=["ops"])
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"])
    async def readyz(request: Request, response: Response):
        checks = await be(request).ready()
        if not all(checks.values()):
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return checks

    @app.get("/metrics", tags=["ops"], include_in_schema=False)
    async def metrics_endpoint():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/me", tags=["auth"])
    async def me(user: Principal = Depends(current_user)):
        return {"username": user.username, "roles": sorted(user.roles), "sees_pii": user.sees_pii}

    # --- Cases ---------------------------------------------------------------------------------
    @app.post("/cases", status_code=status.HTTP_202_ACCEPTED, tags=["cases"])
    async def start_case(alert: AlertEvent, request: Request, _: Principal = Depends(require("l2", "admin"))):
        """Start a case manually (dev/demo): publishes the alert to aml.alerts.v1."""
        return await publish(be(request), ALERTS_TOPIC, alert, "alert")

    @app.get("/cases", tags=["cases"])
    async def list_cases(request: Request, status_: list[str] | None = Query(None, alias="status"),
                         lane: str | None = None, entity: str | None = None, limit: int = Query(200, le=1000),
                         _: Principal = Depends(current_user)):
        """Work queue (FR-100), newest first; filter by status (repeatable), lane and legal entity."""
        return await be(request).cases.queue(status_, lane, entity, limit)

    @app.get("/cases/{case_id}", tags=["cases"])
    async def get_case(case_id: str, request: Request, user: Principal = Depends(current_user)):
        """Review packet: case record, state, where the run is waiting and what can be done next.
        Customer data is shown only to roles allowed to see it (FR-109)."""
        b = be(request)
        row = await record_of(b, case_id)
        snap = await b.graph.aget_state(run_config(row["thread_id"] or case_id))
        values = snap.values or {}
        waiting = snap.interrupts[0].value if snap.interrupts else None
        view = {
            "case": {k: row[k] for k in ("case_id", "status", "tier", "legal_entity", "customer_id", "thread_id")}
                    | {"updated_at": row["updated_at"], "alert": row["alert"]},
            "state": {k: values[k] for k in VIEW_KEYS if k in values}
                     | {"pii_values_redacted": len(values.get("pii_vault") or {})},
            "recommendation": {k: recommendation_of(values).get(k) for k in ("recommendation", "reason_code",
                                                                             "risk_score")} if values else None,
            "waiting_for": None if not waiting else {
                "kind": waiting["kind"], "allowed_actions": waiting.get("allowed_actions"),
                **({"draft": waiting["draft"]} if waiting["kind"] == "approval" else {})},
        }
        vault = PiiVault(mapping=values.get("pii_vault"))
        return vault.restore(view) if user.sees_pii else vault.mask(view)

    @app.get("/cases/{case_id}/history", tags=["cases"])
    async def history(case_id: str, request: Request, _: Principal = Depends(current_user)):
        """Audit timeline (UC-07): one entry per checkpoint of the case's current run, oldest first."""
        b = be(request)
        row = await record_of(b, case_id)
        steps = [{
            "checkpoint_id": s.config["configurable"]["checkpoint_id"],
            "created_at": s.created_at,
            "step": s.metadata.get("step"),
            "source": s.metadata.get("source"),
            "next": list(s.next),
            "completed": sorted({t.name for t in s.tasks if t.result is not None}) if s.tasks else [],
        } async for s in b.graph.aget_state_history(run_config(row["thread_id"] or case_id))]
        return {"thread_id": row["thread_id"] or case_id, "checkpoints": steps[::-1]}

    @app.get("/cases/{case_id}/events", tags=["cases"])
    async def case_events(case_id: str, request: Request, _: Principal = Depends(current_user)):
        """Case events so far, oldest first (the stream below also follows new ones)."""
        b = be(request)
        await record_of(b, case_id)
        return await b.events(case_id)

    @app.get("/cases/{case_id}/stream", tags=["cases"])
    async def stream(case_id: str, request: Request, _: Principal = Depends(current_user)):
        """Live progress (FR-102): server-sent events from aml.case-events.v1, history first."""
        b = be(request)
        await record_of(b, case_id)

        async def events():
            async for event in b.tail(case_id):
                if await request.is_disconnected():
                    break
                yield {"event": event["type"], "data": json.dumps(event)}

        return EventSourceResponse(events(), ping=15)

    @app.post("/cases/{case_id}/decision", status_code=status.HTTP_202_ACCEPTED, tags=["decisions"])
    async def decide(case_id: str, body: DecisionIn, request: Request,
                     user: Principal = Depends(require(*INVESTIGATORS))):
        """Disposition (UC-02). BR-08: L1 may decide only fast-lane cases. BR-09: reason code from the list.
        BR-10: only while the case is awaiting review."""
        b = be(request)
        row = await record_of(b, case_id)
        expect_status(row, "awaiting_review")
        snap = await b.graph.aget_state(run_config(row["thread_id"] or case_id))
        tier = row["tier"] or (snap.values or {}).get("tier")  # cases started before the lane was recorded
        if tier != "fast" and not user.has("l2"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "full-lane cases need an L2 investigator (BR-08)")
        rec = recommendation_of(snap.values or {}).get("recommendation")
        decision = DecisionEvent(case_id=case_id, investigator_id=user.username, **body.model_dump(),
                                 agree_with_recommendation=body.action == rec)
        try:
            validate_decision(decision.model_dump())
        except ValueError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
        return await publish(b, DECISIONS_TOPIC, decision, "decision")

    @app.post("/cases/{case_id}/approval", status_code=status.HTTP_202_ACCEPTED, tags=["decisions"])
    async def approve(case_id: str, body: ApprovalIn, request: Request, user: Principal = Depends(require("l2"))):
        """UC-03: an L2 investigator approves, edits or rejects the drafted customer information request.
        An edited request must still pass the tipping-off rail."""
        b = be(request)
        expect_status(await record_of(b, case_id), "awaiting_approval")
        approval = ApprovalEvent(case_id=case_id, approver_id=user.username, **body.model_dump(exclude_none=True))
        try:
            validate_approval(approval.model_dump())
        except ValueError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
        return await publish(b, DECISIONS_TOPIC, approval, "approval")

    @app.post("/cases/{case_id}/reply", status_code=status.HTTP_202_ACCEPTED, tags=["decisions"])
    async def reply(case_id: str, body: ReplyIn, request: Request,
                    _: Principal = Depends(require("l1", "l2", "admin"))):
        """UC-04: attach the customer's reply; the follow-up worker starts a new run on {case_id}:rN."""
        b = be(request)
        expect_status(await record_of(b, case_id), "info_requested")
        return await publish(b, FOLLOWUPS_TOPIC, FollowUpEvent(case_id=case_id, **body.model_dump()), "reply")

    # --- QA (UC-05) ----------------------------------------------------------------------------
    @app.get("/qa/sample", tags=["qa"])
    async def qa_sample(request: Request, n: int = Query(5, ge=1, le=50), user: Principal = Depends(require("qa"))):
        """Random decided cases this reviewer has not labelled yet."""
        return await be(request).cases.qa_sample(user.username, n)

    @app.post("/cases/{case_id}/qa-label", status_code=status.HTTP_201_CREATED, tags=["qa"])
    async def qa_label(case_id: str, label: QALabel, request: Request, user: Principal = Depends(require("qa"))):
        b = be(request)
        expect_status(await record_of(b, case_id), *STATUS_AFTER_DECISION.values())
        await b.cases.save_label(case_id, user.username, label.model_dump())
        return {"case_id": case_id, "reviewer": user.username, "label": label.model_dump()}

    @app.get("/cases/{case_id}/qa-labels", tags=["qa"])
    async def qa_labels(case_id: str, request: Request, _: Principal = Depends(require("qa", "admin"))):
        b = be(request)
        await record_of(b, case_id)
        return await b.cases.labels(case_id)

    return app
