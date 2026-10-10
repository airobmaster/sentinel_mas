"""Quality gate logic (offline): threshold checks, the gate verdict and LangSmith evaluators."""

import json

from sentinel import evaluate, quality
from sentinel.experiments import acceptable, citations_valid, escalation_recall

LIMITS = {"minimum": {"escalation_recall": 0.9, "narrative_quality": 0.7}, "maximum": {"false_escalation_rate": 0.15,
                                                                                    "errors": 0}}


def test_thresholds_file_is_valid():
    limits = quality.thresholds()
    assert {"escalation_recall", "citation_validity", "narrative_quality"} <= set(limits["minimum"])
    assert limits["maximum"]["errors"] == 0 and 0 < limits["narrative_case_threshold"] <= 1


def test_check_reports_each_breach_and_skips_missing_metrics():
    assert quality.check({"escalation_recall": 0.95, "false_escalation_rate": 0.0, "errors": 0}, LIMITS) == []
    failures = quality.check({"escalation_recall": 0.5, "false_escalation_rate": 0.3, "errors": 1,
                              "narrative_quality": None}, LIMITS)
    assert failures == ["escalation_recall 0.500 < 0.900", "false_escalation_rate 0.300 > 0.150", "errors 1.000 > 0.000"]


def test_gate_verdict_includes_the_narrative_judge(tmp_path, monkeypatch):
    monkeypatch.setattr(quality.settings, "data_backend", "json")  # no evals.runs update
    report = {"run_id": "r1", "metrics": {"escalation_recall": 1.0, "errors": 0}, "path": str(tmp_path / "r.json"),
              "cases": [{"case_id": "A", "narrative_score": 0.9}, {"case_id": "B", "narrative_score": 0.4}]}
    out = quality.finish(report, LIMITS)
    assert out["metrics"]["narrative_quality"] == 0.65 and not out["gate"]["passed"]
    assert out["gate"]["failures"] == ["narrative_quality 0.650 < 0.700"]
    assert json.loads((tmp_path / "r.json").read_text())["gate"]["passed"] is False


def test_narrative_inputs_give_the_judge_the_cited_evidence():
    values = {"alert": {"scenario_code": "TM-STRUCT-01", "scenario_name": "Cash deposits", "legal_entity": "UK"},
              "tier": "fast", "evidence": [{"id": "txn:T1", "summary": "Cash 9,800"}],
              "narrative": {"summary": "Five deposits.", "recommendation": "escalate", "reason_code": "STRUCTURING_CONFIRMED",
                            "claims": [{"text": "Deposits under the threshold", "evidence_ids": ["txn:T1", "txn:T9"]}]}}
    inputs = evaluate.narrative_inputs(values)
    assert "Deposits under the threshold [txn:T1, txn:T9]" in inputs["narrative_text"]
    assert inputs["evidence_context"] == ["txn:T1: Cash 9,800", "txn:T9: NOT IN EVIDENCE"]
    assert "TM-STRUCT-01" in inputs["alert_text"]


def test_langsmith_evaluators():
    must = {"acceptable": ["escalate"]}
    assert acceptable({"recommendation": "escalate"}, must)["score"] == 1
    assert escalation_recall({"recommendation": "close"}, must)["score"] == 0
    assert escalation_recall({"recommendation": "close"}, {"acceptable": ["close"]}) is None  # not a recall case
    assert citations_valid({"bad_citations": 0}, must)["score"] == 1
