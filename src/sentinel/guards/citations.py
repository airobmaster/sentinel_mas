"""Code checks that run before the LLM critic (TDD §6.3, BR-05)."""

from sentinel.schemas import REASON_CODES
from sentinel.state import CaseState, QAIssue

FAST_LANE_SPECIALISTS = ("kyc", "txn", "screening")


def issue(target: str, severity: str, description: str) -> QAIssue:
    return {"target_agent": target, "severity": severity, "description": description}


def check_citations(state: CaseState) -> list[QAIssue]:
    """Every narrative claim, and every ID the typology assessment relies on, must exist in state."""
    known = {e["id"] for e in state.get("evidence", [])}
    issues = []
    for c in (state.get("narrative") or {}).get("claims", []):
        if not c["evidence_ids"]:
            issues.append(issue("narrative", "blocker", f"Uncited claim: {c['text'][:80]}"))
        for eid in c["evidence_ids"]:
            if eid not in known:
                issues.append(issue("narrative", "blocker", f"Unknown evidence id {eid}"))
    typology = state.get("findings", {}).get("typology") or {}
    cited = set(typology.get("policy_refs", []))
    for match in typology.get("typologies", []):
        cited |= set(match.get("evidence_ids", [])) | set(match.get("policy_refs", []))
    for eid in sorted(cited - known):
        issues.append(issue("typology", "blocker", f"Typology assessment cites unknown evidence id {eid}"))
    return issues


def check_completeness(state: CaseState) -> list[QAIssue]:
    """BR-05 checklist. Fast lane: KYC, Txn, Screening and a typology recommendation. Full lane adds
    network findings and at least one policy reference. The narrative must carry the typology's
    recommendation and a reason code allowed for it."""
    findings = state.get("findings", {})
    issues = []
    for agent in FAST_LANE_SPECIALISTS + ("typology",):
        if agent not in findings:
            issues.append(issue(agent, "blocker", f"{agent} findings are missing"))
    typology = findings.get("typology") or {}
    if state.get("tier") == "full":
        if "network" not in findings:
            issues.append(issue("network", "blocker", "network findings are missing (full lane)"))
        if typology and not typology.get("policy_refs"):
            issues.append(issue("typology", "blocker", "no policy reference for the recommendation (full lane)"))
    narrative = state.get("narrative")
    if not narrative or not narrative.get("claims"):
        issues.append(issue("narrative", "blocker", "Narrative has no claims"))
        return issues
    if narrative.get("reason_code") not in REASON_CODES.get(narrative.get("recommendation"), []):
        issues.append(issue("narrative", "major", f"Reason code {narrative.get('reason_code')} is not allowed for "
                                                  f"recommendation {narrative.get('recommendation')}"))
    if typology and (narrative.get("recommendation"), narrative.get("reason_code")) != (
            typology.get("recommendation"), typology.get("reason_code")):
        issues.append(issue("narrative", "major",
                            f"Narrative recommends {narrative.get('recommendation')} ({narrative.get('reason_code')}) "
                            f"but the typology assessment recommends {typology.get('recommendation')} "
                            f"({typology.get('reason_code')})"))
    return issues
