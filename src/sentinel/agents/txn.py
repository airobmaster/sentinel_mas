"""Transaction analytics specialist."""

from sentinel.agents.briefs import brief_for
from sentinel.agents.factory import get_specialist, run_specialist, tool_evidence
from sentinel.config import settings
from sentinel.schemas import TxnFindings
from sentinel.state import CaseState


async def txn(state: CaseState) -> dict:
    spec = await get_specialist("txn", settings.model_txn, "txn", TxnFindings)
    findings, messages = await run_specialist(spec, brief_for("txn", state), state["legal_entity"])
    return {
        "evidence": tool_evidence(messages, "txn"),
        "findings": {"txn": findings.model_dump()},
        "versions": {"txn": spec.versions},
    }
