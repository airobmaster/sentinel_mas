"""Kafka clients: connection settings (local PLAINTEXT or MSK IAM), topic setup and producers."""

import asyncio
import json
import ssl

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.abc import AbstractTokenProvider
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from pydantic import BaseModel

from sentinel.config import settings
from sentinel.events import TOPICS


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
