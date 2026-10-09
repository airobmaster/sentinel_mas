"""Kafka workers (TDD §13, §15).

- Alert worker (group `sentinel-alerts`): starts a case once (idempotent on the checkpoint),
  streams progress to `aml.case-events.v1`, and leaves the case paused for a human: at the
  approval of a customer information request (UC-03) or at the disposition.
- Decision worker (group `sentinel-decisions`): resumes a case only when it is waiting at the
  step the message is for (approval or disposition); stale or duplicate messages are ignored.
- Follow-up worker (group `sentinel-followups`, UC-04): a customer reply to a request for
  information starts a follow-up run on thread `{case_id}:rN` with the reply as evidence.
Delivery is at-least-once: offsets are committed after processing, and the checkpoint makes
replays safe. Messages for the same case in one batch are processed in order.
"""

import asyncio
import logging
from collections import defaultdict
from typing import Awaitable, Callable

from langgraph.types import Command
from pydantic import ValidationError

from sentinel.cli import describe_update
from sentinel.config import settings
from sentinel.events import (ALERTS_TOPIC, CASE_EVENTS_TOPIC, DECISIONS_TOPIC, DLQ_TOPIC, FOLLOWUPS_TOPIC,
                             AlertEvent, CaseEvent, FollowUpEvent, parse_resume)
from sentinel.graph import follow_up_state, follow_up_thread, initial_state, run_config
from sentinel.hitl import REVIEW_NODES, WAITING_NODES, validate_approval, validate_decision
from sentinel.persistence import STATUS_AFTER_DECISION

log = logging.getLogger("sentinel.worker")

Publish = Callable[[CaseEvent], Awaitable[None]]


async def stream(graph, payload, config: dict, case_id: str, publish: Publish, cases=None,
                 level: str | None = None) -> None:
    """Run (or resume) the graph, publishing each completed step. Once triage knows the lane the case is
    assigned to its first review level (BR-08), so only that level sees it in progress."""
    async for update in graph.astream(payload, config, stream_mode="updates"):
        for node, out in update.items():
            if node == "__interrupt__":
                continue
            if node == "triage" and cases is not None and (out or {}).get("tier"):
                await cases.assign(case_id, level or ("l1" if out["tier"] == "fast" else "l2"))
            detail = "; ".join(line.split("]", 1)[-1].strip() for line in describe_update(node, out))
            await publish(CaseEvent(case_id=case_id, type="node_completed", node=node, detail=detail or None))
            for event in (out or {}).get("security_events", []):  # each update carries only its new events
                await publish(CaseEvent(case_id=case_id, type="security_event", node=node,
                                        detail=f"{event['kind']}: {event['detail']}", data=event.get("data")))


async def report_pause(graph, config: dict, case_id: str, publish: Publish, cases) -> str:
    """After a run or resume: record where the case is waiting and who it is with (approval: L2; review:
    the level of that review step)."""
    snapshot = await graph.aget_state(config)
    tier = snapshot.values.get("tier")  # recorded for the work queue and the L1/L2 rule (BR-08)
    if snapshot.next == ("approve_info_request",):
        draft = snapshot.interrupts[0].value["draft"]
        await cases.set_status(case_id, "awaiting_approval", tier=tier, assigned_role="l2")
        await publish(CaseEvent(case_id=case_id, type="awaiting_approval",
                                data={"questions": len(draft["questions"]), "rail_passed": draft["rail"]["passed"]}))
        return "awaiting_approval"
    if snapshot.next and snapshot.next[0] in REVIEW_NODES:
        packet = snapshot.interrupts[0].value
        await cases.set_status(case_id, "awaiting_review", tier=tier, assigned_role=packet["level"],
                               qa_flagged=packet.get("qa_flagged"))
        await publish(CaseEvent(
            case_id=case_id, type="awaiting_review",
            data={"level": packet["level"], "recommendation": packet["recommendation"],
                  "reason_code": packet["reason_code"], "tier": packet["tier"], "qa_issues": len(packet["qa_issues"]),
                  "security_events": len(packet.get("security_events") or [])},
        ))
        return "awaiting_review"
    raise RuntimeError(f"run for {case_id} stopped at {snapshot.next} instead of a human step")


def stopped_midway(snapshot) -> bool:
    """A run that has started but neither paused for a human nor finished: a step failed after its
    retries, or the worker died. Redelivery of its message resumes it from the last checkpoint."""
    return bool(snapshot.values and snapshot.next and not snapshot.interrupts)


async def resume(graph, snapshot, config: dict, case_id: str, publish: Publish, cases) -> str:
    await publish(CaseEvent(case_id=case_id, type="case_resumed",
                            detail=f"resuming from the last checkpoint at {', '.join(snapshot.next)}"))
    await cases.set_status(case_id, "in_progress")
    await stream(graph, None, config, case_id, publish, cases, snapshot.values.get("review_level"))
    return await report_pause(graph, config, case_id, publish, cases)


async def handle_alert(graph, raw: str, publish: Publish, cases) -> str:
    """Process one alert message; returns the resulting case status."""
    alert = AlertEvent.model_validate_json(raw).model_dump(exclude_none=True)
    case_id, config = alert["case_id"], run_config(alert["case_id"])
    snapshot = await graph.aget_state(config)
    if stopped_midway(snapshot):
        return await resume(graph, snapshot, config, case_id, publish, cases)
    if snapshot.values:
        await publish(CaseEvent(case_id=case_id, type="duplicate_ignored", detail="case already started"))
        return "duplicate"
    await cases.start(alert)
    await publish(CaseEvent(case_id=case_id, type="case_started", data={"scenario": alert["scenario_code"]}))
    await stream(graph, initial_state(alert), config, case_id, publish, cases)
    return await report_pause(graph, config, case_id, publish, cases)


async def handle_decision(graph, raw: str, publish: Publish, cases) -> str:
    """Process one disposition or approval; returns the resulting case status (or 'ignored')."""
    message = parse_resume(raw).model_dump(exclude_none=True)
    kind, case_id = message.pop("kind"), message.pop("case_id")
    config = run_config(await cases.thread_for(case_id))
    snapshot = await graph.aget_state(config)
    if not snapshot.next or snapshot.next[0] not in WAITING_NODES[kind]:
        reason = (f"case is not waiting for {'approval' if kind == 'approval' else 'review'}"
                  if snapshot.values else "unknown case")
        await publish(CaseEvent(case_id=case_id, type="decision_ignored", detail=reason))
        return "ignored"
    level = (snapshot.interrupts[0].value.get("level") or "l2") if snapshot.interrupts else "l2"
    try:
        validate_approval(message) if kind == "approval" else validate_decision(message, level)
    except ValueError as e:
        await publish(CaseEvent(case_id=case_id, type="decision_ignored", detail=f"invalid {kind}: {e}"))
        return "ignored"
    await stream(graph, Command(resume=message), config, case_id, publish)
    if kind == "approval":
        await publish(CaseEvent(case_id=case_id, type="approval_applied",
                                data={"action": message["action"], "approver_id": message["approver_id"]}))
        return await report_pause(graph, config, case_id, publish, cases)
    await publish(CaseEvent(case_id=case_id, type="decision_applied",
                            data={"level": level, "action": message["action"], "reason_code": message["reason_code"],
                                  "investigator_id": message["investigator_id"]}))
    if (await graph.aget_state(config)).next:  # escalated: now waiting at the next level
        return await report_pause(graph, config, case_id, publish, cases)
    status = STATUS_AFTER_DECISION[message["action"]]
    await cases.set_status(case_id, status)
    return status


async def handle_followup(graph, raw: str, publish: Publish, cases) -> str:
    """UC-04: start a follow-up run when a case that requested information gets the customer's reply."""
    reply = FollowUpEvent.model_validate_json(raw)
    case_id = reply.case_id
    prior_thread = await cases.thread_for(case_id)
    prior = (await graph.aget_state(run_config(prior_thread))).values
    if not prior or (prior.get("decision") or {}).get("action") != "request_info":
        await publish(CaseEvent(case_id=case_id, type="follow_up_ignored",
                                detail="case is not waiting for a customer reply"))
        return "ignored"
    state = follow_up_state(prior, prior_thread, reply.reply_text, reply.received_at)
    thread = follow_up_thread(case_id, state)
    config = run_config(thread)
    snapshot = await graph.aget_state(config)
    if stopped_midway(snapshot):
        return await resume(graph, snapshot, config, case_id, publish, cases)
    if snapshot.values:
        await publish(CaseEvent(case_id=case_id, type="duplicate_ignored", detail=f"{thread} already started"))
        return "duplicate"
    await cases.save_reply(case_id, state["follow_up"]["round"], reply.reply_text, reply.received_at)
    await cases.start(state["alert"], thread)
    await publish(CaseEvent(case_id=case_id, type="follow_up_started", data={"thread": thread}))
    await stream(graph, state, config, case_id, publish, cases, state.get("review_level"))
    return await report_pause(graph, config, case_id, publish, cases)


def by_case(messages: list) -> list[list]:
    """Group a batch by key so each case's messages run in order, different cases in parallel."""
    groups: dict = defaultdict(list)
    for m in messages:
        groups[m.key].append(m)
    return list(groups.values())


async def process_with_retries(handler, graph, msg, publish: Publish, cases, send_dlq) -> None:
    case_id = msg.key.decode() if msg.key else "unknown"
    raw = msg.value.decode()
    for attempt in range(1, settings.worker_max_attempts + 1):
        try:
            await handler(graph, raw, publish, cases)
            return
        except ValidationError as e:  # malformed message: retrying will not help
            error = f"invalid message: {e.errors()[0]['msg']}"
            break
        except Exception as e:  # noqa: BLE001 - any failure is retried, then dead-lettered
            error = f"{type(e).__name__}: {str(e)[:300]}"
            log.warning("attempt %d failed for %s: %s", attempt, case_id, error)
    await send_dlq(msg.topic, case_id, raw, error)
    await publish(CaseEvent(case_id=case_id, type="error", detail=error))
    try:
        await cases.set_status(case_id, "error")
    except Exception:  # noqa: BLE001 - status update is best effort for unknown cases
        pass


async def run_worker(graph, cases, handlers: dict, concurrency: int, stop: asyncio.Event | None = None) -> None:
    """Consume the topics in `handlers` ({topic: handler}) until `stop` is set."""
    from sentinel import kafka

    groups = {ALERTS_TOPIC: "sentinel-alerts", DECISIONS_TOPIC: "sentinel-decisions",
              FOLLOWUPS_TOPIC: "sentinel-followups"}
    prod = kafka.producer()
    consumers = {t: kafka.consumer(t, group_id=groups[t]) for t in handlers}
    await prod.start()
    for c in consumers.values():
        await c.start()

    async def publish(event: CaseEvent) -> None:
        await kafka.send(prod, CASE_EVENTS_TOPIC, event)
        log.info("%s %s %s", event.case_id, event.type, event.detail or event.data or "")

    async def send_dlq(source_topic: str, case_id: str, raw: str, error: str) -> None:
        await prod.send_and_wait(DLQ_TOPIC, key=case_id, value=raw,
                                 headers=[("source_topic", source_topic.encode()), ("error", error.encode())])

    async def consume(topic: str) -> None:
        consumer, handler = consumers[topic], handlers[topic]
        while not (stop and stop.is_set()):
            batch = await consumer.getmany(timeout_ms=1000, max_records=concurrency)
            messages = [m for part in batch.values() for m in part]
            if not messages:
                continue

            async def run_group(group: list) -> None:
                for m in group:
                    await process_with_retries(handler, graph, m, publish, cases, send_dlq)

            await asyncio.gather(*(run_group(g) for g in by_case(messages)))
            await consumer.commit()

    try:
        await asyncio.gather(*(consume(t) for t in handlers))
    finally:
        for c in consumers.values():
            await c.stop()
        await prod.stop()


HANDLERS = {
    "alerts": {ALERTS_TOPIC: handle_alert},
    "decisions": {DECISIONS_TOPIC: handle_decision},
    "followups": {FOLLOWUPS_TOPIC: handle_followup},
    "all": {ALERTS_TOPIC: handle_alert, DECISIONS_TOPIC: handle_decision, FOLLOWUPS_TOPIC: handle_followup},
}
