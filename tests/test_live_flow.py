"""End-to-end runs against AWS Bedrock. Run with: SENTINEL_LIVE=1 pytest -m live -s"""

import json
import os
from pathlib import Path

import pytest
from langgraph.types import Command

from sentinel.graph import compile_graph, initial_state, run_config

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("SENTINEL_LIVE") != "1", reason="set SENTINEL_LIVE=1 to call Bedrock"),
]
ALERTS = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "alerts"


async def investigate(case_id: str):
    alert = json.loads((ALERTS / f"{case_id}.json").read_text(encoding="utf-8"))
    graph = compile_graph()
    config = run_config(case_id)
    await graph.ainvoke(initial_state(alert), config)
    snap = await graph.aget_state(config)
    assert snap.next == ("human_review",)
    assert snap.values["qa_issues"] == [], snap.values["qa_issues"]
    assert {"kyc", "txn", "screening"} <= snap.values["findings"].keys()
    print(json.dumps(snap.values["narrative"], indent=2))
    return graph, config, snap.values


def true_matches(values: dict) -> list[str]:
    return [h["entry_id"] for h in values["findings"]["screening"]["hits"] if h["is_true_match"]]


async def test_structuring_case_is_escalated_with_valid_citations():
    graph, config, values = await investigate("CASE-0001")
    assert "STRUCTURING" in [f["code"] for f in values["findings"]["txn"]["red_flags"]]
    assert true_matches(values) == []  # Jordan Ellison is a near miss (DOB differs)
    assert values["narrative"]["recommendation"] == "escalate"

    decision = {"action": "escalate", "reason_code": "STRUCTURING_CONFIRMED", "investigator_id": "INV-0001"}
    await graph.ainvoke(Command(resume=decision), config)
    assert (await graph.aget_state(config)).values["decision"] == decision


async def test_benign_case_is_not_escalated():
    _, _, values = await investigate("CASE-0002")
    assert true_matches(values) == []  # Samuel Carter PEP is a near miss
    assert values["narrative"]["recommendation"] in ("close", "request_info")


async def test_sanctions_true_match_is_escalated():
    _, _, values = await investigate("CASE-0003")
    assert values["tier"] == "full"  # high-risk customer (BR-02)
    assert true_matches(values) == ["OFSI-7781"]
    assert values["narrative"]["recommendation"] == "escalate"
    assert values["narrative"]["reason_code"] == "SANCTIONS_TRUE_MATCH"
