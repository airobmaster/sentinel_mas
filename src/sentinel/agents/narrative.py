"""Narrative specialist: case summary with cited claims and a recommendation.

No tools yet; `save_draft_narrative` (case_mgmt) is added with UC-03.
"""

from sentinel.agents.briefs import brief_for
from sentinel.agents.factory import get_specialist, run_specialist
from sentinel.config import settings
from sentinel.schemas import NarrativeDraft
from sentinel.state import CaseState


async def narrative(state: CaseState) -> dict:
    spec = get_specialist("narrative", settings.model_narrative, "narrative", NarrativeDraft)
    draft, _ = await run_specialist(spec, brief_for("narrative", state), state["legal_entity"])
    return {"narrative": draft.model_dump(), "versions": {"narrative": spec.versions}}
