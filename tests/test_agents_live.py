"""Per-agent structured-output tests (D6-01) against Bedrock: each specialist on a fixture case, in-process
tools on the fixture data. Checks the output schema and the finding that defines the case.
Run with: SENTINEL_LIVE=1 pytest -m live tests/test_agents_live.py"""

import os

import pytest

from sentinel import data
from sentinel.agents.kyc import kyc
from sentinel.agents.screening import screening
from sentinel.agents.txn import txn
from sentinel.graph import initial_state
from sentinel.schemas import KycFindings, ScreeningFindings, TxnFindings

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(not os.environ.get("SENTINEL_LIVE"), reason="set SENTINEL_LIVE=1 (calls Bedrock)")]


def state_for(case_id: str, tier: str = "fast") -> dict:
    state = initial_state(data.get_alert(case_id))
    state["tier"] = tier
    return state


def evidence_ids(out: dict) -> set[str]:
    return {e["id"] for e in out.get("evidence", [])}


async def test_kyc_agent_profile_and_cited_evidence():
    out = await kyc(state_for("CASE-0002"))
    findings = KycFindings.model_validate(out["findings"]["kyc"])  # the schema holds
    assert findings.risk_rating.split()[0].lower() == data.get_customer(data.get_alert("CASE-0002")["customer_id"])[
        "risk_rating"].lower()
    cited = {eid for o in findings.expected_vs_actual for eid in o.evidence_ids}
    assert cited and cited <= evidence_ids(out)  # every citation is real


async def test_transaction_agent_detects_structuring():
    out = await txn(state_for("CASE-0001"))
    flags = {f.code for f in TxnFindings.model_validate(out["findings"]["txn"]).red_flags}
    assert "STRUCTURING" in flags


async def test_screening_agent_confirms_the_true_sanctions_match():
    out = await screening(state_for("CASE-0003", tier="full"))
    findings = ScreeningFindings.model_validate(out["findings"]["screening"])
    assert any(h.is_true_match for h in findings.hits), findings.summary
