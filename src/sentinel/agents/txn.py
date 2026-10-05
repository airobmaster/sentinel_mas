"""Transaction analytics specialist."""

from functools import lru_cache

from sentinel.agents.briefs import brief_for
from sentinel.agents.factory import build_specialist, run_specialist, tool_evidence
from sentinel.config import settings
from sentinel.schemas import TxnFindings
from sentinel.state import CaseState
from sentinel.tools.txn_tools import TXN_TOOLS


@lru_cache
def _specialist():
    return build_specialist("txn", settings.model_txn, TXN_TOOLS, "txn", TxnFindings)


async def txn(state: CaseState) -> dict:
    spec = _specialist()
    findings, messages = await run_specialist(spec, brief_for("txn", state))
    return {
        "evidence": tool_evidence(messages, "txn"),
        "findings": {"txn": findings.model_dump()},
        "versions": {"txn": spec.versions},
    }
