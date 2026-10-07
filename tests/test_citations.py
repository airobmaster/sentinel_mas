from sentinel.agents.qa import worst_target
from sentinel.guards.citations import check_citations, check_completeness, issue

EVIDENCE = [{"id": "txn:T1", "source": "test", "agent": "txn", "summary": "s"},
            {"id": "policy:AML-UK@3.2#4.3", "source": "test", "agent": "typology", "summary": "p"}]
TYPOLOGY = {"typologies": [{"code": "STRUCT", "evidence_ids": ["txn:T1"], "policy_refs": ["policy:AML-UK@3.2#4.3"]}],
            "recommendation": "escalate", "reason_code": "STRUCTURING_CONFIRMED",
            "policy_refs": ["policy:AML-UK@3.2#4.3"]}
ALL_FINDINGS = {"kyc": {}, "txn": {}, "screening": {}, "typology": TYPOLOGY}


def state_with(claims, recommendation="escalate", reason_code="STRUCTURING_CONFIRMED", findings=None, tier="fast"):
    return {
        "tier": tier,
        "evidence": EVIDENCE,
        "findings": ALL_FINDINGS if findings is None else findings,
        "narrative": {"claims": claims, "recommendation": recommendation, "reason_code": reason_code},
    }


OK_CLAIM = [{"text": "ok", "evidence_ids": ["txn:T1"]}]


def test_valid_citations_pass():
    s = state_with(OK_CLAIM)
    assert check_citations(s) == [] and check_completeness(s) == []


def test_uncited_and_unknown_ids_are_blockers():
    s = state_with([{"text": "no cite", "evidence_ids": []}, {"text": "bad", "evidence_ids": ["txn:NOPE"]}])
    issues = check_citations(s)
    assert len(issues) == 2 and all(i["severity"] == "blocker" for i in issues)
    assert "Unknown evidence id txn:NOPE" in issues[1]["description"]


def test_typology_citations_are_checked():
    bad = {**TYPOLOGY, "policy_refs": ["policy:MADE-UP@1#1"]}
    issues = check_citations(state_with(OK_CLAIM, findings={**ALL_FINDINGS, "typology": bad}))
    assert [(i["target_agent"], i["severity"]) for i in issues] == [("typology", "blocker")]


def test_reason_code_must_match_recommendation():
    s = state_with(OK_CLAIM, reason_code="FP_DATA_ERROR")
    issues = check_completeness(s)
    assert issues[0]["severity"] == "major" and "not allowed" in issues[0]["description"]


def test_narrative_must_follow_the_typology_recommendation():
    s = state_with(OK_CLAIM, recommendation="close", reason_code="FP_EXPLAINED_ACTIVITY")
    issues = check_completeness(s)
    assert [(i["target_agent"], i["severity"]) for i in issues] == [("narrative", "major")]


def test_missing_specialist_findings_target_that_specialist():
    s = state_with(OK_CLAIM, findings={k: v for k, v in ALL_FINDINGS.items() if k != "txn"})
    assert [i["target_agent"] for i in check_completeness(s)] == ["txn"]
    s = state_with(OK_CLAIM, findings={})
    assert [i["target_agent"] for i in check_completeness(s)] == ["kyc", "txn", "screening", "typology"]


def test_full_lane_needs_network_and_a_policy_reference():
    no_refs = {**TYPOLOGY, "policy_refs": []}
    s = state_with(OK_CLAIM, tier="full", findings={**ALL_FINDINGS, "typology": no_refs})
    assert {(i["target_agent"], i["description"].split(" (")[0]) for i in check_completeness(s)} == {
        ("network", "network findings are missing"), ("typology", "no policy reference for the recommendation")}


def test_worst_target_prefers_blockers_and_ignores_minor():
    issues = [issue("narrative", "major", "a"), issue("txn", "blocker", "b")]
    assert worst_target(issues) == "txn"
    assert worst_target([issue("narrative", "minor", "style")]) is None
    assert worst_target([]) is None
