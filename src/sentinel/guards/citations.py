"""Code checks that run before any LLM critic (TDD §6.3, BR-05)."""

from sentinel.schemas import REASON_CODES
from sentinel.state import CaseState, QAIssue

FAST_LANE_SPECIALISTS = ("kyc", "txn", "screening")


def issue(target: str, severity: str, description: str) -> QAIssue:
    return {"target_agent": target, "severity": severity, "description": description}


def check_citations(state: CaseState) -> list[QAIssue]:
    """Every narrative claim must cite at least one evidence ID that exists in state."""
    known = {e["id"] for e in state.get("evidence", [])}
    issues = []
    for c in (state.get("narrative") or {}).get("claims", []):
        if not c["evidence_ids"]:
            issues.append(issue("narrative", "blocker", f"Uncited claim: {c['text'][:80]}"))
        for eid in c["evidence_ids"]:
            if eid not in known:
                issues.append(issue("narrative", "blocker", f"Unknown evidence id {eid}"))
    return issues


def check_completeness(state: CaseState) -> list[QAIssue]:
    """BR-05 fast-lane checklist: KYC, Txn and Screening findings and a valid recommendation are present.
    (The full-lane extras, network findings and a policy reference, arrive with those agents.)"""
    issues = []
    for agent in FAST_LANE_SPECIALISTS:
        if agent not in state.get("findings", {}):
            issues.append(issue(agent, "blocker", f"{agent} findings are missing"))
    narrative = state.get("narrative")
    if not narrative or not narrative.get("claims"):
        issues.append(issue("narrative", "blocker", "Narrative has no claims"))
    elif narrative.get("reason_code") not in REASON_CODES.get(narrative.get("recommendation"), []):
        issues.append(
            issue(
                "narrative",
                "major",
                f"Reason code {narrative.get('reason_code')} is not allowed for "
                f"recommendation {narrative.get('recommendation')}",
            )
        )
    return issues
