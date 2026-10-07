"""Kafka clients: connection settings (local PLAINTEXT or MSK IAM), topic setup and producers."""

import asyncio
import json
import ssl

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition
from aiokafka.abc import AbstractTokenProvider
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from pydantic import BaseModel

from sentinel.config import settings
from sentinel.events import CASE_EVENTS_TOPIC, TOPICS


class MskIamTokenProvider(AbstractTokenProvider):
    """OAUTHBEARER tokens for MSK Serverless IAM auth (used on AWS; not exercised locally)."""

    async def token(self) -> str:
        from aws_msk_iam_sasl_signer import MSKAuthTokenProvider

        token, _ = await asyncio.to_thread(MSKAuthTokenProvider.generate_auth_token, settings.aws_region)
        return token


def connection_kwargs() -> dict:
    kwargs: dict = {"bootstrap_servers": settings.kafka_bootstrap}
    if settings.kafka_security == "msk_iam":
        kwargs |= {
            "security_protocol": "SASL_SSL",
            "sasl_mechanism": "OAUTHBEARER",
            "sasl_oauth_token_provider": MskIamTokenProvider(),
            "ssl_context": ssl.create_default_context(),
        }
    return kwargs


def producer() -> AIOKafkaProducer:
    return AIOKafkaProducer(
        **connection_kwargs(),
        key_serializer=lambda k: k.encode(),
        value_serializer=lambda v: v.encode(),
        acks="all",
        enable_idempotence=True,
    )


def consumer(*topics: str, group_id: str | None, auto_offset_reset: str = "earliest") -> AIOKafkaConsumer:
    return AIOKafkaConsumer(
        *topics, **connection_kwargs(), group_id=group_id, enable_auto_commit=False,
        auto_offset_reset=auto_offset_reset,
    )


async def send(prod: AIOKafkaProducer, topic: str, event: BaseModel) -> None:
    """Publish a validated event keyed by case_id (per-case ordering, TDD §3.1)."""
    await prod.send_and_wait(topic, key=event.case_id, value=event.model_dump_json(exclude_none=True))


async def create_topics() -> list[str]:
    """Create any missing Sentinel topics; returns the ones created."""
    admin = AIOKafkaAdminClient(**connection_kwargs())
    await admin.start()
    try:
        missing = [t for t in TOPICS if t not in set(await admin.list_topics())]
        if missing:
            await admin.create_topics([NewTopic(t, settings.kafka_partitions, 1) for t in missing])
    finally:
        await admin.close()
    return missing


def decode(value: bytes) -> dict:
    return json.loads(value.decode())


async def publish(topic: str, events: list[BaseModel]) -> None:
    """Publish validated events (one short-lived producer; for tools and the console)."""
    prod = producer()
    await prod.start()
    try:
        for event in events:
            await send(prod, topic, event)
    finally:
        await prod.stop()


async def read_events(case_id: str | None = None) -> list[dict]:
    """All case events currently on the topic (optionally for one case), oldest first."""
    admin = AIOKafkaAdminClient(**connection_kwargs())
    await admin.start()
    try:
        [meta] = await admin.describe_topics([CASE_EVENTS_TOPIC])
    finally:
        await admin.close()
    parts = [TopicPartition(CASE_EVENTS_TOPIC, p["partition"]) for p in meta.get("partitions", [])]
    if not parts:
        return []
    reader = AIOKafkaConsumer(**connection_kwargs(), group_id=None, enable_auto_commit=False)
    await reader.start()
    try:
        reader.assign(parts)
        await reader.seek_to_beginning(*parts)
        end = await reader.end_offsets(parts)
        events = []
        while any([await reader.position(p) < end[p] for p in parts]):
            for messages in (await reader.getmany(*parts, timeout_ms=1000)).values():
                for m in messages:
                    event = decode(m.value)
                    if case_id is None or event["case_id"] == case_id:
                        events.append(event)
        return sorted(events, key=lambda e: e["at"])
    finally:
        await reader.stop()
