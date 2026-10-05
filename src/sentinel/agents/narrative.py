"""Narrative specialist: case summary with cited claims and a recommendation.

No tools yet; `save_draft_narrative` (case_mgmt) is added with the MCP layer.
"""

from functools import lru_cache

from sentinel.agents.briefs import brief_for
from sentinel.agents.factory import build_specialist, run_specialist
from sentinel.config import settings
from sentinel.schemas import NarrativeDraft
from sentinel.state import CaseState


@lru_cache
def _specialist():
    return build_specialist("narrative", settings.model_narrative, [], "narrative", NarrativeDraft)


async def narrative(state: CaseState) -> dict:
    spec = _specialist()
    draft, _ = await run_specialist(spec, brief_for("narrative", state))
    return {"narrative": draft.model_dump(), "versions": {"narrative": spec.versions}}
