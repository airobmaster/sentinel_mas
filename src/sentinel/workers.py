"""Kafka workers (TDD §13, §15).

- Alert worker (group `sentinel-alerts`): starts a case once (idempotent on the checkpoint),
  streams progress to `aml.case-events.v1`, and leaves the case paused at human review.
- Decision worker (group `sentinel-decisions`): resumes a case only when it is waiting at
  human review; stale or duplicate decisions are ignored.
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
from sentinel.events import (ALERTS_TOPIC, CASE_EVENTS_TOPIC, DECISIONS_TOPIC, DLQ_TOPIC, AlertEvent,
                             CaseEvent, DecisionEvent)
from sentinel.graph import initial_state, run_config
from sentinel.hitl import validate_decision
from sentinel.persistence import STATUS_AFTER_DECISION

log = logging.getLogger("sentinel.worker")

Publish = Callable[[CaseEvent], Awaitable[None]]


async def stream(graph, payload, config: dict, case_id: str, publish: Publish) -> None:
    async for update in graph.astream(payload, config, stream_mode="updates"):
        for node, out in update.items():
            if node != "__interrupt__":
                detail = "; ".join(line.split("]", 1)[-1].strip() for line in describe_update(node, out))
                await publish(CaseEvent(case_id=case_id, type="node_completed", node=node, detail=detail or None))


async def handle_alert(graph, raw: str, publish: Publish, cases) -> str:
    """Process one alert message; returns the resulting case status."""
    alert = AlertEvent.model_validate_json(raw).model_dump(exclude_none=True)
    case_id, config = alert["case_id"], run_config(alert["case_id"])
    if (await graph.aget_state(config)).values:
        await publish(CaseEvent(case_id=case_id, type="duplicate_ignored", detail="case already started"))
        return "duplicate"
    await cases.start(alert)
    await publish(CaseEvent(case_id=case_id, type="case_started", data={"scenario": alert["scenario_code"]}))
    await stream(graph, initial_state(alert), config, case_id, publish)
    snapshot = await graph.aget_state(config)
    if snapshot.next != ("human_review",):
        raise RuntimeError(f"run for {case_id} stopped at {snapshot.next} instead of human review")
    packet = snapshot.interrupts[0].value
    await cases.set_status(case_id, "awaiting_review")
    await publish(CaseEvent(
        case_id=case_id, type="awaiting_review",
        data={"recommendation": packet["recommendation"], "reason_code": packet["reason_code"],
              "tier": packet["tier"], "qa_issues": len(packet["qa_issues"])},
    ))
    return "awaiting_review"


async def handle_decision(graph, raw: str, publish: Publish, cases) -> str:
    """Process one decision message; returns the resulting case status (or 'ignored')."""
    decision = DecisionEvent.model_validate_json(raw).model_dump(exclude_none=True)
    case_id = decision.pop("case_id")
    config = run_config(case_id)
    snapshot = await graph.aget_state(config)
    if snapshot.next != ("human_review",):
        reason = "case is not waiting for review" if snapshot.values else "unknown case"
        await publish(CaseEvent(case_id=case_id, type="decision_ignored", detail=reason))
        return "ignored"
    try:
        validate_decision(decision)
    except ValueError as e:
        await publish(CaseEvent(case_id=case_id, type="decision_ignored", detail=f"invalid decision: {e}"))
        return "ignored"
    await stream(graph, Command(resume=decision), config, case_id, publish)
    status = STATUS_AFTER_DECISION[decision["action"]]
    await cases.set_status(case_id, status)
    await publish(CaseEvent(case_id=case_id, type="decision_applied",
                            data={"action": decision["action"], "reason_code": decision["reason_code"],
                                  "investigator_id": decision["investigator_id"]}))
    return status


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

    groups = {ALERTS_TOPIC: "sentinel-alerts", DECISIONS_TOPIC: "sentinel-decisions"}
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


HANDLERS = {"alerts": {ALERTS_TOPIC: handle_alert}, "decisions": {DECISIONS_TOPIC: handle_decision},
            "all": {ALERTS_TOPIC: handle_alert, DECISIONS_TOPIC: handle_decision}}
