from sentinel.agents.qa import worst_target
from sentinel.guards.citations import check_citations, check_completeness, issue

EVIDENCE = [{"id": "txn:T1", "source": "test", "agent": "txn", "summary": "s"}]


def state_with(claims, recommendation="escalate", reason_code="STRUCTURING_CONFIRMED", findings=None):
    return {
        "evidence": EVIDENCE,
        "findings": {"kyc": {}, "txn": {}, "screening": {}} if findings is None else findings,
        "narrative": {"claims": claims, "recommendation": recommendation, "reason_code": reason_code},
    }


def test_valid_citations_pass():
    s = state_with([{"text": "ok", "evidence_ids": ["txn:T1"]}])
    assert check_citations(s) == [] and check_completeness(s) == []


def test_uncited_and_unknown_ids_are_blockers():
    s = state_with([{"text": "no cite", "evidence_ids": []}, {"text": "bad", "evidence_ids": ["txn:NOPE"]}])
    issues = check_citations(s)
    assert len(issues) == 2 and all(i["severity"] == "blocker" for i in issues)
    assert "Unknown evidence id txn:NOPE" in issues[1]["description"]


def test_reason_code_must_match_recommendation():
    s = state_with([{"text": "ok", "evidence_ids": ["txn:T1"]}], recommendation="close")
    issues = check_completeness(s)
    assert len(issues) == 1 and issues[0]["severity"] == "major"


def test_missing_specialist_findings_target_that_specialist():
    s = state_with([{"text": "ok", "evidence_ids": ["txn:T1"]}], findings={"kyc": {}, "screening": {}})
    assert [i["target_agent"] for i in check_completeness(s)] == ["txn"]
    s = state_with([{"text": "ok", "evidence_ids": ["txn:T1"]}], findings={})
    assert [i["target_agent"] for i in check_completeness(s)] == ["kyc", "txn", "screening"]


def test_worst_target_prefers_blockers():
    issues = [issue("narrative", "major", "a"), issue("txn", "blocker", "b")]
    assert worst_target(issues) == "txn"
    assert worst_target([]) is None
