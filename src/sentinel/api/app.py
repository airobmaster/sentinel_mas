"""Sentinel API (TDD §12): the investigator workbench's only door into the system.

Reads come from Postgres (case records) and the LangGraph checkpointer (case state). Every change
goes out as a Kafka message that the workers apply, exactly as in the Kafka flow: the API checks
roles and business rules first so a bad request is refused here, not ignored later by a worker.

Run locally with `sentinel api` (http://localhost:8000/docs).
"""

import asyncio
import hashlib
import json
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sse_starlette.sse import EventSourceResponse

from sentinel.api.auth import (REVIEW_LEVELS, ROLES, TEST_USERS, Principal, current_user, dev_token, issuer, require,
                               token_for)
from sentinel.api.backend import Backend, live_backend
from sentinel.api.models import ApprovalIn, DecisionIn, DevTokenIn, QALabel, ReplyIn
from sentinel.config import settings
from sentinel.events import (ALERTS_TOPIC, DECISIONS_TOPIC, FOLLOWUPS_TOPIC, AlertEvent, ApprovalEvent,
                             DecisionEvent, FollowUpEvent)
from sentinel.graph import run_config
from sentinel.guardrails.pii import PiiVault
from sentinel.hitl import recommendation_of, validate_approval, validate_decision
from sentinel.persistence import DECIDED_STATUSES
from sentinel.schemas import LEVEL_ACTIONS

INVESTIGATORS = ("l1", "l2", "mlro")
# Parts of the case state the workbench shows; pii_vault (the token -> value map) never leaves the API
VIEW_KEYS = ("tier", "narrative", "findings", "evidence", "qa_issues", "qa_rounds", "info_request", "follow_up",
             "decision", "decisions", "security_events", "usage", "versions", "budget_exceeded")

REQUESTS = Counter("sentinel_api_requests_total", "API requests", ["method", "route", "status"])
LATENCY = Histogram("sentinel_api_request_seconds", "API request latency", ["method", "route"])
SUBMITTED = Counter("sentinel_api_submissions_total", "Messages published by the API", ["kind"])


def visible(user: Principal, row: dict) -> bool:
    """BR-17: a review level sees only the cases assigned to it (no visibility of other levels' cases);
    QA reviewers see decided cases; administrators see everything."""
    if user.has("admin"):
        return True
    if user.has("qa") and row["status"] in DECIDED_STATUSES:
        return True
    return bool(row.get("assigned_role")) and user.has(row["assigned_role"])


def waiting_level(waiting: dict, row: dict) -> str:
    """The review level a paused case is with: the interrupt says so; older checkpoints fall back to the record."""
    return waiting.get("level") or row.get("assigned_role") or ("l1" if row.get("tier") == "fast" else "l2")


def is_blind(case_id: str) -> bool:
    """FR-107: a stable share of cases is reviewed without the agents' draft and recommendation, to
    measure how often investigators agree with the system when they have not seen its answer."""
    return int(hashlib.sha256(case_id.encode()).hexdigest(), 16) % 100 < settings.blind_mode_percent


def blind_view(view: dict) -> dict:
    state = {k: v for k, v in view["state"].items() if k not in ("narrative", "qa_issues")}
    if typology := (state.get("findings") or {}).get("typology"):
        state["findings"] = {**state["findings"], "typology": {k: v for k, v in typology.items() if k not in (
            "recommendation", "reason_code", "risk_score", "rationale")}}
    return {**view, "state": state, "recommendation": None, "blind": True}


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

    async def record_of(b: Backend, case_id: str, user: Principal | None = None) -> dict:
        """The case record; 404 also when the user may not see the case (its existence is not disclosed)."""
        row = await b.cases.get(case_id)
        if row is None or (user is not None and not visible(user, row)):
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

    @app.get("/auth/config", tags=["auth"])
    async def auth_config():
        """Public sign-in settings for the workbench (none are secrets): it configures itself from these."""
        if settings.auth_mode == "dev":
            return {"mode": "dev", "roles": list(ROLES), "demo_role_switch": True}
        return {"mode": "cognito", "region": settings.aws_region, "user_pool_id": settings.cognito_user_pool_id,
                "client_id": settings.cognito_workbench_client_id, "domain": settings.cognito_domain,
                "authority": issuer(), "demo_role_switch": settings.demo_role_switch}

    @app.post("/auth/demo-sign-in", tags=["auth"])
    async def demo_sign_in(body: DevTokenIn):
        """Demo role switch (SENTINEL_DEMO_ROLE_SWITCH=true, local stack only): signs in as the role's test
        user and returns a real Cognito access token, so every role rule still applies."""
        if settings.auth_mode == "dev":
            return {"access_token": dev_token(TEST_USERS[body.role], [body.role]), "username": TEST_USERS[body.role]}
        if not settings.demo_role_switch:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "the demo role switch is off")
        try:
            token = await asyncio.to_thread(token_for, body.role)
        except Exception as e:  # noqa: BLE001 - missing test user or Cognito refusal
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"demo sign-in failed: {e}") from e
        return {"access_token": token, "username": TEST_USERS[body.role]}

    @app.post("/auth/dev-token", tags=["auth"])
    async def dev_sign_in(body: DevTokenIn):
        """Dev mode only (no Cognito): a token for a role's test user. Not available with Cognito."""
        if settings.auth_mode != "dev":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "dev sign-in is off when Cognito is configured")
        return {"access_token": dev_token(TEST_USERS[body.role], [body.role]), "username": TEST_USERS[body.role]}

    # --- Cases ---------------------------------------------------------------------------------
    @app.post("/cases", status_code=status.HTTP_202_ACCEPTED, tags=["cases"])
    async def start_case(alert: AlertEvent, request: Request, _: Principal = Depends(require("l2", "admin"))):
        """Start a case manually (dev/demo): publishes the alert to aml.alerts.v1."""
        return await publish(be(request), ALERTS_TOPIC, alert, "alert")

    @app.get("/cases", tags=["cases"])
    async def list_cases(request: Request, status_: list[str] | None = Query(None, alias="status"),
                         lane: str | None = None, entity: str | None = None, limit: int = Query(200, le=1000),
                         user: Principal = Depends(current_user)):
        """Work queue (FR-100), newest first; filter by status (repeatable), lane and legal entity. Each review
        level sees only the cases assigned to it (BR-17); QA reviewers see decided cases; admins see all."""
        roles = None if user.has("admin") else [r for r in REVIEW_LEVELS if user.has(r)]
        return await be(request).cases.queue(status_, lane, entity, limit, roles=None if user.has("qa") else roles,
                                             decided_only=user.has("qa") and not user.has("admin"))

    @app.get("/cases/{case_id}", tags=["cases"])
    async def get_case(case_id: str, request: Request, user: Principal = Depends(current_user)):
        """Review packet: case record, state, where the run is waiting and what can be done next.
        Customer data is shown only to roles allowed to see it (FR-109)."""
        b = be(request)
        row = await record_of(b, case_id, user)
        snap = await b.graph.aget_state(run_config(row["thread_id"] or case_id))
        values = snap.values or {}
        waiting = snap.interrupts[0].value if snap.interrupts else None
        view = {
            "case": {k: row.get(k) for k in ("case_id", "status", "tier", "assigned_role", "legal_entity", "customer_id",
                                              "thread_id")}
                    | {"updated_at": row["updated_at"], "alert": row["alert"]},
            "state": {k: values[k] for k in VIEW_KEYS if k in values}
                     | {"pii_values_redacted": len(values.get("pii_vault") or {})},
            "recommendation": {k: recommendation_of(values).get(k) for k in ("recommendation", "reason_code",
                                                                             "risk_score")} if values else None,
            "waiting_for": None if not waiting else {
                "kind": waiting["kind"], "allowed_actions": waiting.get("allowed_actions"),
                "level": waiting_level(waiting, row),
                **({"draft": waiting["draft"]} if waiting["kind"] == "approval"
                   else {"reason_codes": LEVEL_ACTIONS[waiting_level(waiting, row)]})},
        }
        if row["status"] == "awaiting_review" and is_blind(case_id):  # FR-107: decide without the draft
            view = blind_view(view)
        vault = PiiVault(mapping=values.get("pii_vault"))
        return vault.restore(view) if user.sees_pii else vault.mask(view)

    @app.get("/cases/{case_id}/network", tags=["cases"])
    async def case_network(case_id: str, request: Request, user: Principal = Depends(current_user)):
        """Linked customers and shared devices within 2 hops (FR-104), for the network graph view."""
        from sentinel.graphdb import network_graph

        row = await record_of(be(request), case_id, user)
        return network_graph(row["customer_id"]) or {"customer_id": row["customer_id"], "nodes": [], "edges": []}

    @app.get("/cases/{case_id}/history", tags=["cases"])
    async def history(case_id: str, request: Request, user: Principal = Depends(current_user)):
        """Audit timeline (UC-07): one entry per checkpoint of the case's current run, oldest first."""
        b = be(request)
        row = await record_of(b, case_id, user)
        steps = [{
            "checkpoint_id": s.config["configurable"]["checkpoint_id"],
            "created_at": s.created_at,
            "step": s.metadata.get("step"),
            "source": s.metadata.get("source"),
            "next": list(s.next),
        } async for s in b.graph.aget_state_history(run_config(row["thread_id"] or case_id))][::-1]
        # A checkpoint is saved after a step: the nodes that just ran are the previous checkpoint's `next`
        for previous, current in zip([None, *steps], steps):
            current["completed"] = sorted(previous["next"]) if previous else []
        return {"thread_id": row["thread_id"] or case_id, "checkpoints": steps}

    @app.get("/cases/{case_id}/events", tags=["cases"])
    async def case_events(case_id: str, request: Request, user: Principal = Depends(current_user)):
        """Case events so far, oldest first (the stream below also follows new ones)."""
        b = be(request)
        await record_of(b, case_id, user)
        return await b.events(case_id)

    @app.get("/cases/{case_id}/stream", tags=["cases"])
    async def stream(case_id: str, request: Request, user: Principal = Depends(current_user)):
        """Live progress (FR-102): server-sent events from aml.case-events.v1, history first."""
        b = be(request)
        await record_of(b, case_id, user)

        async def events():
            async for event in b.tail(case_id):
                if await request.is_disconnected():
                    break
                yield {"event": event["type"], "data": json.dumps(event)}

        return EventSourceResponse(events(), ping=15)

    @app.post("/cases/{case_id}/decision", status_code=status.HTTP_202_ACCEPTED, tags=["decisions"])
    async def decide(case_id: str, body: DecisionIn, request: Request,
                     user: Principal = Depends(require(*INVESTIGATORS))):
        """Disposition (UC-02) at the level the case is with (BR-08 first level by lane, BR-16 escalation L1 ->
        L2 -> MLRO). BR-09: an action and reason code allowed at that level. BR-10: only while awaiting review."""
        b = be(request)
        row = await record_of(b, case_id, user)
        expect_status(row, "awaiting_review")
        snap = await b.graph.aget_state(run_config(row["thread_id"] or case_id))
        level = waiting_level(snap.interrupts[0].value if snap.interrupts else {}, row)
        if not user.has(level):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"this case is with {level.upper()} (BR-08, BR-16)")
        rec = recommendation_of(snap.values or {}).get("recommendation")
        decision = DecisionEvent(case_id=case_id, investigator_id=user.username, **body.model_dump(),
                                 agree_with_recommendation=body.action == rec)
        try:
            validate_decision(decision.model_dump(), level)
        except ValueError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
        return await publish(b, DECISIONS_TOPIC, decision, "decision")

    @app.post("/cases/{case_id}/approval", status_code=status.HTTP_202_ACCEPTED, tags=["decisions"])
    async def approve(case_id: str, body: ApprovalIn, request: Request, user: Principal = Depends(require("l2"))):
        """UC-03: an L2 investigator approves, edits or rejects the drafted customer information request.
        An edited request must still pass the tipping-off rail."""
        b = be(request)
        expect_status(await record_of(b, case_id, user), "awaiting_approval")
        approval = ApprovalEvent(case_id=case_id, approver_id=user.username, **body.model_dump(exclude_none=True))
        try:
            validate_approval(approval.model_dump())
        except ValueError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
        return await publish(b, DECISIONS_TOPIC, approval, "approval")

    @app.post("/cases/{case_id}/reply", status_code=status.HTTP_202_ACCEPTED, tags=["decisions"])
    async def reply(case_id: str, body: ReplyIn, request: Request,
                    user: Principal = Depends(require("l1", "l2", "admin"))):
        """UC-04: attach the customer's reply; the follow-up worker starts a new run on {case_id}:rN."""
        b = be(request)
        expect_status(await record_of(b, case_id, user), "info_requested")
        return await publish(b, FOLLOWUPS_TOPIC, FollowUpEvent(case_id=case_id, **body.model_dump()), "reply")

    # --- QA (UC-05) ----------------------------------------------------------------------------
    @app.get("/qa/sample", tags=["qa"])
    async def qa_sample(request: Request, n: int = Query(5, ge=1, le=50), user: Principal = Depends(require("qa"))):
        """Decided cases for QA, not yet labelled by this reviewer: every case whose automated QA raised issues,
        plus a 10% sample of the rest (UC-05, BR-18)."""
        return await be(request).cases.qa_sample(user.username, n)

    @app.post("/cases/{case_id}/qa-label", status_code=status.HTTP_201_CREATED, tags=["qa"])
    async def qa_label(case_id: str, label: QALabel, request: Request, user: Principal = Depends(require("qa"))):
        b = be(request)
        expect_status(await record_of(b, case_id), *DECIDED_STATUSES)
        await b.cases.save_label(case_id, user.username, label.model_dump())
        return {"case_id": case_id, "reviewer": user.username, "label": label.model_dump()}

    @app.get("/cases/{case_id}/qa-labels", tags=["qa"])
    async def qa_labels(case_id: str, request: Request, _: Principal = Depends(require("qa", "admin"))):
        b = be(request)
        await record_of(b, case_id)
        return await b.cases.labels(case_id)

    return app
