"""What the API reads and writes: the case records (Postgres), the case state (the LangGraph
checkpointer, read only: the API never runs agents) and Kafka (decisions, approvals, replies and
alerts go out as messages; the workers apply them; case events come back)."""

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from pydantic import BaseModel

from sentinel.events import CASE_EVENTS_TOPIC


@dataclass
class Backend:
    cases: object  # persistence.CaseStore (or a test double with the same methods)
    graph: object  # compiled graph with the shared checkpointer
    send: Callable[[str, BaseModel], Awaitable[None]]  # publish an event to a topic
    tail: Callable[[str], AsyncIterator[dict]]  # case events for one case: history, then live
    ready: Callable[[], Awaitable[dict[str, bool]]]
    events: Callable[[str], Awaitable[list[dict]]]  # case events so far (no waiting)


async def kafka_tail(case_id: str) -> AsyncIterator[dict]:
    """Every event for the case from the start of the topic, then new ones as they arrive."""
    from sentinel import kafka

    consumer = kafka.consumer(CASE_EVENTS_TOPIC, group_id=None, auto_offset_reset="earliest")
    await consumer.start()
    try:
        async for msg in consumer:
            event = json.loads(msg.value)
            if event["case_id"] == case_id:
                yield event
    finally:
        await consumer.stop()


@asynccontextmanager
async def live_backend():
    """Postgres pool + checkpointer + one long-lived Kafka producer for the API process."""
    from sentinel import kafka
    from sentinel.graph import build_graph
    from sentinel.persistence import durable_state

    async with durable_state() as (checkpointer, cases):
        producer = kafka.producer()
        await producer.start()

        async def send(topic: str, event: BaseModel) -> None:
            await kafka.send(producer, topic, event)

        async def ready() -> dict[str, bool]:
            checks = {}
            try:
                async with cases.pool.connection() as conn:
                    await conn.execute("SELECT 1")
                checks["postgres"] = True
            except Exception:  # noqa: BLE001 - reported, not raised
                checks["postgres"] = False
            try:
                await producer.partitions_for(CASE_EVENTS_TOPIC)
                checks["kafka"] = True
            except Exception:  # noqa: BLE001
                checks["kafka"] = False
            return checks

        try:
            yield Backend(cases, build_graph().compile(checkpointer=checkpointer), send, kafka_tail, ready,
                          kafka.read_events)
        finally:
            await producer.stop()
