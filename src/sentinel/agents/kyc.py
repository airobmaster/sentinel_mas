"""KYC context specialist."""

from sentinel.agents.briefs import brief_for
from sentinel.agents.factory import get_specialist, run_specialist, tool_evidence
from sentinel.config import settings
from sentinel.schemas import KycFindings
from sentinel.state import CaseState


async def kyc(state: CaseState) -> dict:
    spec = await get_specialist("kyc", settings.model_kyc, "kyc", KycFindings)
    findings, messages = await run_specialist(spec, brief_for("kyc", state), state["legal_entity"])
    return {
        "evidence": tool_evidence(messages, "kyc"),
        "findings": {"kyc": findings.model_dump()},
        "versions": {"kyc": spec.versions},
    }
