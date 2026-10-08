"""KYC context specialist."""

from sentinel.agents.briefs import brief_for
from sentinel.agents.factory import get_specialist, run_specialist, tool_evidence
from sentinel.config import settings
from sentinel.schemas import KycFindings
from sentinel.state import CaseState


async def kyc(state: CaseState) -> dict:
    spec = get_specialist("kyc", settings.model_kyc, "kyc", KycFindings)
    findings, messages, run = await run_specialist(spec, brief_for("kyc", state), state)
    return {
        "evidence": tool_evidence(messages, "kyc"),
        "findings": {"kyc": findings.model_dump()},
        "versions": {"kyc": spec.versions},
        **run.update(),
    }
