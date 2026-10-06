"""Kafka message schemas (TDD §3.1, §3.2, §7.2, §12). Validated on both produce and consume."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ALERTS_TOPIC = "aml.alerts.v1"
DECISIONS_TOPIC = "aml.decisions.v1"
CASE_EVENTS_TOPIC = "aml.case-events.v1"
DLQ_TOPIC = "aml.alerts.dlq.v1"
TOPICS = (ALERTS_TOPIC, DECISIONS_TOPIC, CASE_EVENTS_TOPIC, DLQ_TOPIC)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AlertEvent(BaseModel):
    """A transaction-monitoring alert that starts a case (key: case_id)."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    alert_id: str
    legal_entity: str
    customer_id: str
    account_ids: list[str] = Field(min_length=1)
    scenario_code: str
    scenario_name: str
    score: float = Field(ge=0, le=1)
    triggered_at: str
    lookback_days: int = Field(ge=1, le=400)
    triggering_txn_ids: list[str]
    schema_version: Literal["1.0"]
    sanctions_indicator: bool | None = None
    pep_indicator: bool | None = None


class DecisionEvent(BaseModel):
    """The investigator's disposition that resumes a paused case (key: case_id)."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    action: Literal["close", "escalate", "request_info"]
    reason_code: str
    investigator_id: str
    narrative_edits: str | None = None
    agree_with_recommendation: bool | None = None
    decided_at: str = Field(default_factory=now)


CaseEventType = Literal[
    "case_started", "node_completed", "awaiting_review", "decision_applied",
    "duplicate_ignored", "decision_ignored", "error",
]


class CaseEvent(BaseModel):
    """Progress event for the API / workbench (key: case_id)."""

    case_id: str
    type: CaseEventType
    node: str | None = None
    detail: str | None = None
    data: dict | None = None
    at: str = Field(default_factory=now)
