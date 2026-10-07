"""Human-in-the-loop steps (TDD §7): the disposition (every case) and the approval of a customer
information request (UC-03).

UC-03 is a step in the main graph rather than LangChain's in-agent HumanInTheLoopMiddleware: an
in-agent pause would sit inside a specialist's tool loop, which runs without a checkpointer, so it
would not survive a restart or travel over Kafka. As a graph step it is checkpointed in Postgres,
resumable through the decisions topic, and auditable like the disposition."""

from datetime import datetime, timezone

from langgraph.types import interrupt

from sentinel.guardrails.output_rails import check_customer_text
from sentinel.guardrails.pii import PiiVault
from sentinel.schemas import REASON_CODES
from sentinel.state import CaseState

APPROVAL_ACTIONS = {"approve": "approved", "edit": "approved_with_edits", "reject": "rejected"}
# Which interrupt each kind of resume message is for
WAITING_NODE = {"decision": "human_review", "approval": "approve_info_request"}


def validate_decision(decision: dict) -> None:
    action = decision.get("action")
    if action not in REASON_CODES:
        raise ValueError(f"Action must be one of {list(REASON_CODES)}, got {action!r}")
    if decision.get("reason_code") not in REASON_CODES[action]:
        raise ValueError(f"Reason code for {action} must be one of {REASON_CODES[action]}")
    if not decision.get("investigator_id"):
        raise ValueError("investigator_id is required")


def validate_approval(approval: dict) -> None:
    """An edited request must still pass the tipping-off rail (BR-11)."""
    if approval.get("action") not in APPROVAL_ACTIONS:
        raise ValueError(f"Approval action must be one of {list(APPROVAL_ACTIONS)}")
    if not approval.get("approver_id"):
        raise ValueError("approver_id is required")
    if approval["action"] == "edit":
        text = " ".join([approval.get("message") or "", *(approval.get("questions") or [])])
        if not text.strip():
            raise ValueError("an edited request needs the edited message")
        if violations := check_customer_text(text):
            raise ValueError(f"the edited request would tip off the customer: {', '.join(violations)}")


def recommendation_of(state: CaseState) -> dict:
    """The Typology & Policy agent makes the recommendation (the narrative carries the same one)."""
    return state.get("findings", {}).get("typology") or state.get("narrative") or {}


async def approve_info_request(state: CaseState) -> dict:
    request = state["info_request"]
    approval = interrupt({"kind": "approval", "case_id": state["case_id"], "draft": request,
                          "allowed_actions": list(APPROVAL_ACTIONS)})
    validate_approval(approval)
    edited = approval["action"] == "edit"
    approved = {
        **request,
        "status": APPROVAL_ACTIONS[approval["action"]],
        "message": approval.get("message") if edited else request["message"],
        "questions": approval.get("questions") or request["questions"],
        "approver_id": approval["approver_id"],
        "decided_at": approval.get("decided_at") or datetime.now(timezone.utc).isoformat(),
    }
    if approval["action"] != "reject":  # the text the customer receives, with PII tokens swapped back
        vault = PiiVault(mapping=state.get("pii_vault"))
        approved["outgoing"] = {"message": vault.restore(approved["message"]),
                                "questions": vault.restore(approved["questions"])}
    return {"info_request": approved}


async def human_review(state: CaseState) -> dict:
    source = recommendation_of(state)
    decision = interrupt(
        {
            "kind": "decision",
            "case_id": state["case_id"],
            "tier": state.get("tier"),
            "narrative": state.get("narrative") or {},
            "evidence": state.get("evidence", []),
            "findings": state.get("findings", {}),
            "qa_issues": state.get("qa_issues", []),
            "recommendation": source.get("recommendation"),
            "reason_code": source.get("reason_code"),
            "info_request": state.get("info_request"),
            "security_events": state.get("security_events", []),
            "budget_exceeded": state.get("budget_exceeded", False),
            "allowed_actions": list(REASON_CODES),
        }
    )
    validate_decision(decision)
    return {"decision": decision}
