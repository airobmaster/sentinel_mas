"""Network analysis specialist (full lane only)."""

from sentinel.agents.briefs import brief_for
from sentinel.agents.factory import get_specialist, run_specialist, tool_evidence
from sentinel.config import settings
from sentinel.schemas import NetworkFindings
from sentinel.state import CaseState


async def network(state: CaseState) -> dict:
    spec = get_specialist("network", settings.model_network, "network", NetworkFindings)
    findings, messages, run = await run_specialist(spec, brief_for("network", state), state)
    return {
        "evidence": tool_evidence(messages, "network"),
        "findings": {"network": findings.model_dump()},
        "versions": {"network": spec.versions},
        **run.update(),
    }
