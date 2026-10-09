"""Graph routing with stubbed agents (no Bedrock calls)."""

import json
from pathlib import Path

import pytest
from langgraph.types import Command

from sentinel.graph import compile_graph, initial_state, run_config
from tests import stubs

ALERT = json.loads(
    (Path(__file__).resolve().parents[1] / "data" / "fixtures" / "alerts" / "CASE-0001.json").read_text()
)
DECISION = {"action": "escalate", "reason_code": "STRUCTURING_CONFIRMED", "investigator_id": "INV-0001"}


async def kyc_missing_first_time(state, _calls=[]):  # noqa: B006 - call counter shared by design
    _calls.append(1)
    return {} if len(_calls) == 1 else await stubs.specialist("kyc", "crm:N1")(state)


async def run_to_review(alert=ALERT, **overrides):
    graph = compile_graph(nodes=stubs.nodes(**overrides))
    config = run_config(alert["case_id"])
    await graph.ainvoke(initial_state(alert), config)
    return graph, config, await graph.aget_state(config)


async def test_clean_run_pauses_for_review_then_completes():
    graph, config, snap = await run_to_review()
    assert snap.next == ("human_review",)
    packet = snap.interrupts[0].value
    assert packet["recommendation"] == "escalate" and packet["qa_issues"] == []
    assert snap.values["qa_rounds"] == 1 and snap.values["tier"] == "fast"
    assert "network" not in snap.values["findings"]  # fast lane skips the network agent

    await graph.ainvoke(Command(resume=DECISION), config)  # L1 escalates: the case moves to L2 (BR-16)
    escalated = await graph.aget_state(config)
    assert escalated.next == ("l2_review",) and escalated.values["decision"] == {**DECISION, "level": "l1"}
    close = {"action": "close", "reason_code": "FP_EXPLAINED_ACTIVITY", "investigator_id": "INV-0002"}
    await graph.ainvoke(Command(resume=close), config)
    final = await graph.aget_state(config)
    assert final.next == () and [(d["level"], d["action"]) for d in final.values["decisions"]] == [
        ("l1", "escalate"), ("l2", "close")]


async def test_full_lane_runs_network_before_typology():
    alert = {**ALERT, "case_id": "CASE-FULL", "sanctions_indicator": True}  # BR-02 forces the full lane
    _, _, snap = await run_to_review(alert)
    assert snap.values["tier"] == "full" and "network" in snap.values["findings"]
    assert snap.values["qa_issues"] == []


async def test_fan_out_merges_all_specialists():
    _, _, snap = await run_to_review()
    assert {"triage", "kyc", "txn", "screening", "typology"} <= snap.values["findings"].keys()
    ids = {e["id"] for e in snap.values["evidence"]}
    assert {"cust:CUST-00042", "crm:N1", "txn:TXN-1006", "list:OFSI:X", "policy:TEST@1#1"} <= ids


async def test_bad_citation_is_reworked_once_then_passes():
    narrative = stubs.narrative(["txn:NOPE", "txn:TXN-1006"])
    _, _, snap = await run_to_review(narrative=narrative)
    assert narrative.calls == ["txn:NOPE", "txn:TXN-1006"]
    assert snap.next == ("human_review",)
    assert snap.values["qa_rounds"] == 2 and snap.values["qa_issues"] == []


async def test_missing_specialist_is_reworked_then_typology_and_narrative_rerun():
    narrative = stubs.narrative(["txn:TXN-1006"])
    kyc_missing_first_time.__defaults__[0].clear()
    _, _, snap = await run_to_review(kyc=kyc_missing_first_time, narrative=narrative)
    assert len(narrative.calls) == 2  # rework kyc -> typology -> narrative again -> qa
    assert "kyc" in snap.values["findings"] and snap.values["qa_issues"] == []


async def test_two_qa_failures_go_to_human_with_issues():
    narrative = stubs.narrative(["txn:NOPE"])
    _, _, snap = await run_to_review(narrative=narrative)
    assert len(narrative.calls) == 2  # original + one rework, no more
    assert snap.next == ("human_review",)
    assert "Unknown evidence id txn:NOPE" in [i["description"] for i in snap.interrupts[0].value["qa_issues"]]


async def test_narrative_must_carry_the_typology_recommendation():
    async def disagreeing(state):
        out = await stubs.narrative(["txn:TXN-1006"])(state)
        out["narrative"] |= {"recommendation": "close", "reason_code": "FP_EXPLAINED_ACTIVITY"}
        return out

    _, _, snap = await run_to_review(narrative=disagreeing)
    issues = snap.values["qa_issues"]
    assert snap.values["qa_rounds"] == 2 and issues[0]["target_agent"] == "narrative"
    assert "typology assessment recommends escalate" in issues[0]["description"]
    assert snap.interrupts[0].value["recommendation"] == "escalate"  # the packet shows the typology's view


async def test_invalid_decision_is_rejected():
    graph, config, _ = await run_to_review()
    with pytest.raises(ValueError, match="Reason code"):
        await graph.ainvoke(Command(resume={**DECISION, "reason_code": "FP_DATA_ERROR"}), config)
