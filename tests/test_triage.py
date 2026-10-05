import json
from pathlib import Path

import pytest

from sentinel.agents.triage import lane_rules, triage
from sentinel.graph import initial_state

ALERTS = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "alerts"
BASE_ALERT = {"customer_id": "C1", "scenario_code": "TM-STRUCT-01"}
LOW_RISK = {"risk_rating": "low", "prior_alerts_12m": 0}


def load(case_id: str) -> dict:
    return json.loads((ALERTS / f"{case_id}.json").read_text(encoding="utf-8"))


def test_no_rule_hits_is_fast_lane():
    assert lane_rules(BASE_ALERT, LOW_RISK, 1_000) == []


@pytest.mark.parametrize(
    "alert, customer, amount, expected",
    [
        (BASE_ALERT, {**LOW_RISK, "risk_rating": "high"}, 1_000, "risk rating is high"),
        ({**BASE_ALERT, "sanctions_indicator": True}, LOW_RISK, 1_000, "sanctions/PEP"),
        (BASE_ALERT, LOW_RISK, 50_000, "alert amount"),
        (BASE_ALERT, {**LOW_RISK, "prior_alerts_12m": 2}, 1_000, "prior alerts"),
        ({**BASE_ALERT, "scenario_code": "TM-MULE-02"}, LOW_RISK, 1_000, "network-type"),
        ({**BASE_ALERT, "customer_ids": ["C1", "C2"]}, LOW_RISK, 1_000, "multiple customers"),
    ],
)
def test_each_br02_rule_forces_full_lane(alert, customer, amount, expected):
    hits = lane_rules(alert, customer, amount)
    assert len(hits) == 1 and expected in hits[0]


async def test_triage_structuring_case_is_fast_lane():
    out = await triage(initial_state(load("CASE-0001")))
    assert out["tier"] == "fast"
    assert out["findings"]["triage"]["triggering_total"] == 47_550
    assert out["evidence"][0]["id"] == "cust:CUST-00042"
