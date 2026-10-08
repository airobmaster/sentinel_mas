"""UC-03: draft a customer information request when the recommendation is request_info.

The draft passes an output rail (no tipping-off, no legal conclusions, BR-11) before an investigator
approves, edits or rejects it in the approval step of the main graph (hitl.approve_info_request).
A draft that fails the rail is redrafted once with the offending words named."""

from sentinel.agents.briefs import brief_for
from sentinel.agents.factory import get_specialist, run_specialist
from sentinel.config import settings
from sentinel.guardrails.output_rails import check_customer_text
from sentinel.schemas import CustomerInfoRequest
from sentinel.state import CaseState, add_events, add_usage

MAX_ATTEMPTS = 2


async def draft_info_request(state: CaseState) -> dict:
    spec = get_specialist("customer_request", settings.model_narrative, "customer_request", CustomerInfoRequest)
    feedback, events, usage, vault = "", [], {}, {}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        draft, _, run = await run_specialist(spec, brief_for("customer_request", state) + feedback, state)
        events, usage, vault = add_events(events, run.security_events), add_usage(usage, run.usage), run.pii_vault
        violations = check_customer_text(" ".join([draft.message, *draft.questions]))
        if not violations:
            break
        feedback = ("\n\nYour previous draft used words that must never appear in customer-facing text: "
                    f"{', '.join(violations)}. Rewrite it without them.")
    return {
        "info_request": {"questions": draft.questions, "message": draft.message, "status": "awaiting_approval",
                         "rail": {"passed": not violations, "violations": violations, "attempts": attempt}},
        "versions": {"customer_request": spec.versions},
        "security_events": events, "usage": usage, "pii_vault": vault,
    }
