"""Human-in-the-loop disposition (TDD §7.1, §7.2)."""

from langgraph.types import interrupt

from sentinel.schemas import REASON_CODES
from sentinel.state import CaseState


def validate_decision(decision: dict) -> None:
    action = decision.get("action")
    if action not in REASON_CODES:
        raise ValueError(f"Action must be one of {list(REASON_CODES)}, got {action!r}")
    if decision.get("reason_code") not in REASON_CODES[action]:
        raise ValueError(f"Reason code for {action} must be one of {REASON_CODES[action]}")
    if not decision.get("investigator_id"):
        raise ValueError("investigator_id is required")


async def human_review(state: CaseState) -> dict:
    narrative = state.get("narrative") or {}
    decision = interrupt(
        {
            "case_id": state["case_id"],
            "tier": state.get("tier"),
            "narrative": narrative,
            "evidence": state.get("evidence", []),
            "findings": state.get("findings", {}),
            "qa_issues": state.get("qa_issues", []),
            # Slice 1: the narrative recommends; the typology agent takes this over later.
            "recommendation": narrative.get("recommendation"),
            "reason_code": narrative.get("reason_code"),
            "allowed_actions": list(REASON_CODES),
        }
    )
    validate_decision(decision)
    return {"decision": decision}
