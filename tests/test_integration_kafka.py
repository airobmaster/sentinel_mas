"""Kafka + Postgres round trip with stubbed agents (no Bedrock): alert -> review -> decision.
Needs the Docker stack, loaded data and topics (`sentinel kafka init`). Run with: pytest -m integration"""

import asyncio
import json
import socket
import uuid

import pytest

from sentinel import kafka, persistence
from sentinel.config import REPO_ROOT
from sentinel.events import ALERTS_TOPIC, CASE_EVENTS_TOPIC, DECISIONS_TOPIC, AlertEvent, DecisionEvent
from sentinel.graph import compile_graph, run_config
from sentinel.persistence import durable_state
from sentinel.workers import HANDLERS, run_worker
from tests import stubs

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(any(socket.socket().connect_ex(("localhost", p)) for p in (5432, 9092)),
                       reason="Postgres or Kafka not running"),
]


STUBS = stubs.nodes()


async def wait_for(consumer, case_id: str, event_type: str, timeout: float = 60) -> dict:
    async def scan():
        async for msg in consumer:
            event = kafka.decode(msg.value)
            if event["case_id"] == case_id and event["type"] == event_type:
                return event
    return await asyncio.wait_for(scan(), timeout)


async def test_alert_and_decision_round_trip_through_kafka():
    base = json.loads((REPO_ROOT / "data" / "fixtures" / "alerts" / "CASE-0001.json").read_text(encoding="utf-8"))
    case_id = f"CASE-IT-{uuid.uuid4().hex[:8]}"
    alert = AlertEvent.model_validate({**base, "case_id": case_id})
    stop = asyncio.Event()

    async with durable_state() as (checkpointer, cases):
        graph = compile_graph(checkpointer=checkpointer, nodes=STUBS)
        worker = asyncio.create_task(run_worker(graph, cases, HANDLERS["all"], concurrency=2, stop=stop))
        events = kafka.consumer(CASE_EVENTS_TOPIC, group_id=None)
        prod = kafka.producer()
        await events.start()
        await prod.start()
        try:
            await kafka.send(prod, ALERTS_TOPIC, alert)
            review = await wait_for(events, case_id, "awaiting_review")
            assert review["data"]["recommendation"] == "escalate"
            assert await cases.status(case_id) == "awaiting_review"

            await kafka.send(prod, ALERTS_TOPIC, alert)  # redelivery must not start a second run
            await wait_for(events, case_id, "duplicate_ignored")

            await kafka.send(prod, DECISIONS_TOPIC, DecisionEvent(
                case_id=case_id, action="escalate", reason_code="STRUCTURING_CONFIRMED", investigator_id="INV-IT"))
            applied = await wait_for(events, case_id, "decision_applied")
            assert applied["data"]["action"] == "escalate"
            assert await cases.status(case_id) == "escalated"

            final = await graph.aget_state(run_config(case_id))  # state survives in Postgres
            assert final.values["decision"]["investigator_id"] == "INV-IT" and final.next == ()

            # Helpers used by the console and scripts
            types = [e["type"] for e in await kafka.read_events(case_id)]
            assert types[0] == "case_started" and types[-1] == "decision_applied"
            assert {"awaiting_review", "duplicate_ignored"} <= set(types)
            assert persistence.case_status(case_id) == "escalated"
            assert case_id in [r["case_id"] for r in persistence.list_cases(["escalated"])]
            persistence.reset_case(case_id)
            assert persistence.case_status(case_id) == "new"
            assert not (await graph.aget_state(run_config(case_id))).values
        finally:
            stop.set()
            await asyncio.wait_for(worker, 15)
            await events.stop()
            await prod.stop()
            async with cases.pool.connection() as conn:  # leave the loaded dataset as it was
                await conn.execute("DELETE FROM cases.alerts WHERE case_id = %s", (case_id,))
