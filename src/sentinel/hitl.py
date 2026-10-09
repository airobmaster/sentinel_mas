"""Human-in-the-loop steps (TDD §7): the review levels L1 -> L2 -> MLRO (BR-08, BR-16) and the approval of
a customer information request (UC-03).

UC-03 is a step in the main graph rather than LangChain's in-agent HumanInTheLoopMiddleware: an
in-agent pause would sit inside a specialist's tool loop, which runs without a checkpointer, so it
would not survive a restart or travel over Kafka. As a graph step it is checkpointed in Postgres,
resumable through the decisions topic, and auditable like the disposition."""

from datetime import datetime, timezone

from langgraph.types import interrupt

from sentinel.guardrails.output_rails import check_customer_text
from sentinel.guardrails.pii import PiiVault
from sentinel.schemas import LEVEL_ACTIONS
from sentinel.state import CaseState

APPROVAL_ACTIONS = {"approve": "approved", "edit": "approved_with_edits", "reject": "rejected"}


def validate_decision(decision: dict, level: str = "l2") -> None:
    """BR-09: an action allowed at this review level, with one of its reason codes."""
    codes = LEVEL_ACTIONS[level]
    action = decision.get("action")
    if action not in codes:
        raise ValueError(f"Action at {level.upper()} must be one of {list(codes)}, got {action!r}")
    if decision.get("reason_code") not in codes[action]:
        raise ValueError(f"Reason code for {action} must be one of {codes[action]}")
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


# --- Review levels (BR-08, BR-16) ----------------------------------------------------------------
# The first review is by lane: fast-lane cases go to an L1 analyst, full-lane cases straight to an L2
# investigator (BR-08). Escalating sends the case one level up: L1 -> L2 -> MLRO. Each level is its own
# checkpointed pause, and each level sees only the cases assigned to it.
REVIEW_NODES = {"human_review": None, "l2_review": "l2", "mlro_review": "mlro"}  # None: first level, by lane
NEXT_LEVEL_NODE = {"l1": "l2_review", "l2": "mlro_review"}
# Which interrupts each kind of resume message is for
WAITING_NODES = {"decision": tuple(REVIEW_NODES), "approval": ("approve_info_request",)}


def first_level(state: CaseState) -> str:
    """BR-08 by lane; a follow-up run goes back to the level that asked the customer."""
    return state.get("review_level") or ("l1" if state.get("tier") == "fast" else "l2")


def qa_flagged(state: CaseState) -> bool:
    """The automated QA did not pass cleanly: unresolved serious issues, or the critic failed or was unavailable
    (these cases always go to the QA reviewers, UC-05)."""
    critic = state.get("findings", {}).get("qa") or {}
    serious = any(i["severity"] in ("blocker", "major") for i in state.get("qa_issues") or [])
    return serious or critic.get("passed") is False or bool(critic.get("error"))


def review_step(fixed_level: str | None):
    async def review(state: CaseState) -> dict:
        level = fixed_level or first_level(state)
        source = recommendation_of(state)
        decision = interrupt(
            {
                "kind": "decision",
                "level": level,
                "case_id": state["case_id"],
                "tier": state.get("tier"),
                "narrative": state.get("narrative") or {},
                "evidence": state.get("evidence", []),
                "findings": state.get("findings", {}),
                "qa_issues": state.get("qa_issues", []),
                "qa_flagged": qa_flagged(state),
                "recommendation": source.get("recommendation"),
                "reason_code": source.get("reason_code"),
                "info_request": state.get("info_request"),
                "decisions": state.get("decisions", []),
                "security_events": state.get("security_events", []),
                "budget_exceeded": state.get("budget_exceeded", False),
                "allowed_actions": list(LEVEL_ACTIONS[level]),
            }
        )
        validate_decision(decision, level)
        decision = {**decision, "level": level}
        return {"decision": decision, "decisions": [decision]}

    return review


human_review = review_step(None)  # first level (name kept: cases already paused there resume as before)
l2_review = review_step("l2")
mlro_review = review_step("mlro")


def route_after_review(state: CaseState) -> str:
    """Escalate sends the case up one level; any other decision ends the run."""
    decision = state.get("decision") or {}
    if decision.get("action") == "escalate" and decision.get("level") in NEXT_LEVEL_NODE:
        return NEXT_LEVEL_NODE[decision["level"]]
    return "__end__"
