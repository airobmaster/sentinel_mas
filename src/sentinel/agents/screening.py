"""Sanctions, PEP and adverse-media screening specialist."""

from sentinel.agents.briefs import brief_for
from sentinel.agents.factory import get_specialist, run_specialist, tool_evidence
from sentinel.config import settings
from sentinel.schemas import ScreeningFindings
from sentinel.state import CaseState


async def screening(state: CaseState) -> dict:
    spec = await get_specialist("screening", settings.model_screening, "screening", ScreeningFindings)
    findings, messages = await run_specialist(spec, brief_for("screening", state), state["legal_entity"])
    return {
        "evidence": tool_evidence(messages, "screening"),
        "findings": {"screening": findings.model_dump()},
        "versions": {"screening": spec.versions},
    }
