import json

import pytest
from pydantic import ValidationError

from sentinel import kafka
from sentinel.config import REPO_ROOT, settings
from sentinel.events import AlertEvent, DecisionEvent

ALERTS = REPO_ROOT / "data" / "fixtures" / "alerts"


@pytest.mark.parametrize("path", sorted(ALERTS.glob("*.json")), ids=lambda p: p.stem)
def test_fixture_alerts_are_valid_events(path):
    alert = json.loads(path.read_text(encoding="utf-8"))
    assert AlertEvent.model_validate(alert).model_dump(exclude_none=True) == alert


def test_alert_schema_is_strict():
    alert = json.loads((ALERTS / "CASE-0001.json").read_text(encoding="utf-8"))
    with pytest.raises(ValidationError):
        AlertEvent.model_validate({**alert, "unexpected": 1})
    with pytest.raises(ValidationError):
        AlertEvent.model_validate({k: v for k, v in alert.items() if k != "customer_id"})
    with pytest.raises(ValidationError):
        AlertEvent.model_validate({**alert, "lookback_days": 1000})


def test_decision_schema():
    d = DecisionEvent(case_id="C1", action="escalate", reason_code="STRUCTURING_CONFIRMED", investigator_id="I1")
    assert d.decided_at
    with pytest.raises(ValidationError):
        DecisionEvent(case_id="C1", action="approve_payment", reason_code="X", investigator_id="I1")


def test_msk_iam_connection_settings(monkeypatch):
    monkeypatch.setattr(settings, "kafka_security", "msk_iam")
    kwargs = kafka.connection_kwargs()
    assert kwargs["security_protocol"] == "SASL_SSL" and kwargs["sasl_mechanism"] == "OAUTHBEARER"
    monkeypatch.setattr(settings, "kafka_security", "plaintext")
    assert set(kafka.connection_kwargs()) == {"bootstrap_servers"}
