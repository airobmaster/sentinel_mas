"""Graph routing with stubbed agents (no Bedrock calls)."""

import json
from pathlib import Path

import pytest
from langgraph.types import Command

from sentinel.graph import compile_graph, initial_state, run_config

ALERT = json.loads(
    (Path(__file__).resolve().parents[1] / "data" / "fixtures" / "alerts" / "CASE-0001.json").read_text()
)
DECISION = {"action": "escalate", "reason_code": "STRUCTURING_CONFIRMED", "investigator_id": "INV-0001"}


def stub(agent: str, eid: str):
    """Specialist stub that returns one evidence item and its findings key."""

    async def node(state):
        return {
            "evidence": [{"id": eid, "source": "test", "agent": agent, "summary": f"{agent} evidence"}],
            "findings": {agent: {"stub": True}},
        }

    return node


async def kyc_missing_first_time(state, _calls=[]):  # noqa: B006 - call counter shared by design
    _calls.append(1)
    return {} if len(_calls) == 1 else await stub("kyc", "crm:N1")(state)


def fake_narrative(citations: list[str]):
    """Narrative stub that cites citations[n] on its n-th call (last one repeats)."""
    calls = []

    async def narrative(state):
        cite = citations[min(len(calls), len(citations) - 1)]
        calls.append(cite)
        return {
            "narrative": {
                "summary": "s",
                "claims": [{"text": "Five cash deposits below threshold", "evidence_ids": [cite]}],
                "recommendation": "escalate",
                "reason_code": "STRUCTURING_CONFIRMED",
                "open_questions": [],
            }
        }

    narrative.calls = calls
    return narrative


async def run_to_review(narrative, **overrides):
    nodes = {
        "kyc": stub("kyc", "crm:N1"),
        "txn": stub("txn", "txn:TXN-1006"),
        "screening": stub("screening", "list:OFSI:X"),
        "narrative": narrative,
        **overrides,
    }
    graph = compile_graph(nodes=nodes)
    config = run_config(ALERT["case_id"])
    await graph.ainvoke(initial_state(ALERT), config)
    return graph, config, await graph.aget_state(config)


async def test_clean_run_pauses_for_review_then_completes():
    graph, config, snap = await run_to_review(fake_narrative(["txn:TXN-1006"]))
    assert snap.next == ("human_review",)
    packet = snap.interrupts[0].value
    assert packet["recommendation"] == "escalate" and packet["qa_issues"] == []
    assert snap.values["qa_rounds"] == 1 and snap.values["tier"] == "fast"

    await graph.ainvoke(Command(resume=DECISION), config)
    final = await graph.aget_state(config)
    assert final.next == () and final.values["decision"] == DECISION


async def test_fan_out_merges_all_three_specialists():
    _, _, snap = await run_to_review(fake_narrative(["crm:N1"]))
    assert {"triage", "kyc", "txn", "screening"} <= snap.values["findings"].keys()
    ids = {e["id"] for e in snap.values["evidence"]}
    assert {"cust:CUST-00042", "crm:N1", "txn:TXN-1006", "list:OFSI:X"} <= ids


async def test_bad_citation_is_reworked_once_then_passes():
    narrative = fake_narrative(["txn:NOPE", "txn:TXN-1006"])
    _, _, snap = await run_to_review(narrative)
    assert narrative.calls == ["txn:NOPE", "txn:TXN-1006"]
    assert snap.next == ("human_review",)
    assert snap.values["qa_rounds"] == 2 and snap.values["qa_issues"] == []


async def test_missing_specialist_is_reworked_then_narrative_rewritten():
    narrative = fake_narrative(["txn:TXN-1006"])
    kyc_missing_first_time.__defaults__[0].clear()
    _, _, snap = await run_to_review(narrative, kyc=kyc_missing_first_time)
    assert len(narrative.calls) == 2  # rework kyc -> narrative again -> qa
    assert "kyc" in snap.values["findings"] and snap.values["qa_issues"] == []


async def test_two_qa_failures_go_to_human_with_issues():
    narrative = fake_narrative(["txn:NOPE"])
    _, _, snap = await run_to_review(narrative)
    assert len(narrative.calls) == 2  # original + one rework, no more
    assert snap.next == ("human_review",)
    assert snap.interrupts[0].value["qa_issues"][0]["description"] == "Unknown evidence id txn:NOPE"


async def test_invalid_decision_is_rejected():
    graph, config, _ = await run_to_review(fake_narrative(["txn:TXN-1006"]))
    with pytest.raises(ValueError, match="Reason code"):
        await graph.ainvoke(Command(resume={**DECISION, "reason_code": "FP_DATA_ERROR"}), config)
