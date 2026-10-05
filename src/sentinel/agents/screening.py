"""Sanctions, PEP and adverse-media screening specialist."""

from functools import lru_cache

from sentinel.agents.briefs import brief_for
from sentinel.agents.factory import build_specialist, run_specialist, tool_evidence
from sentinel.config import settings
from sentinel.schemas import ScreeningFindings
from sentinel.state import CaseState
from sentinel.tools.screening_tools import SCREENING_TOOLS


@lru_cache
def _specialist():
    return build_specialist("screening", settings.model_screening, SCREENING_TOOLS, "screening", ScreeningFindings)


async def screening(state: CaseState) -> dict:
    spec = _specialist()
    findings, messages = await run_specialist(spec, brief_for("screening", state))
    return {
        "evidence": tool_evidence(messages, "screening"),
        "findings": {"screening": findings.model_dump()},
        "versions": {"screening": spec.versions},
    }
