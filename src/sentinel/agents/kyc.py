"""KYC context specialist."""

from functools import lru_cache

from sentinel.agents.briefs import brief_for
from sentinel.agents.factory import build_specialist, run_specialist, tool_evidence
from sentinel.config import settings
from sentinel.schemas import KycFindings
from sentinel.state import CaseState
from sentinel.tools.kyc_tools import KYC_TOOLS


@lru_cache
def _specialist():
    return build_specialist("kyc", settings.model_kyc, KYC_TOOLS, "kyc", KycFindings)


async def kyc(state: CaseState) -> dict:
    spec = _specialist()
    findings, messages = await run_specialist(spec, brief_for("kyc", state))
    return {
        "evidence": tool_evidence(messages, "kyc"),
        "findings": {"kyc": findings.model_dump()},
        "versions": {"kyc": spec.versions},
    }
