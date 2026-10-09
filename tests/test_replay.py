"""Golden set and replay selection (offline, on the fixture cases)."""

import json

from sentinel import evaluate
from sentinel.evaluate import make_golden, scored, select_cases, truth_row


def test_golden_split_is_disjoint_complete_and_repeatable(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluate, "GOLDEN_PATH", tmp_path / "golden.json")
    golden = make_golden(holdout=1, seed=7)
    assert not set(golden["dev"]) & set(golden["holdout"]) and len(golden["holdout"]) == 1
    assert sorted(golden["dev"] + golden["holdout"]) == ["CASE-0001", "CASE-0002", "CASE-0003"]
    assert json.loads((tmp_path / "golden.json").read_text()) == golden
    assert make_golden(holdout=1, seed=7) == golden  # same seed, same split

    assert sorted(select_cases("all")) == ["CASE-0001", "CASE-0002", "CASE-0003"]
    assert len(select_cases("dev", n=1)) == 1 and set(select_cases("holdout")) == set(golden["holdout"])


def test_scored_row_from_a_state():
    values = {"narrative": {"recommendation": "escalate", "reason_code": "STRUCTURING_CONFIRMED", "claims": []},
              "tier": "fast", "qa_rounds": 1, "evidence": [],
              "usage": {"kyc": {"input_tokens": 10, "output_tokens": 5}},
              "security_events": [{"kind": "injection_detected"}]}
    row = truth_row("CASE-0001", {"typology": "STRUCT", "expected": "escalate", "acceptable": ["escalate"]}) | scored(values)
    assert row["recommendation"] == "escalate" and row["tokens"] == 15 and row["security_events"] == ["injection_detected"]
    assert evaluate.metrics([row | {"seconds": 1}])["escalation_recall"] == 1.0
