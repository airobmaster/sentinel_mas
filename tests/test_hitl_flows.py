"""UC-03 (approval of a customer information request) and UC-04 (follow-up after the customer's
reply), through the graph and the workers, with stubbed agents."""

import json

import pytest
from langgraph.types import Command

from sentinel import workers
from sentinel.agents.briefs import brief_for
from sentinel.config import REPO_ROOT
from sentinel.graph import compile_graph, follow_up_state, follow_up_thread, initial_state, run_config
from tests import stubs
from tests.test_workers import FakeCases

ALERT = json.loads((REPO_ROOT / "data" / "fixtures" / "alerts" / "CASE-0002.json").read_text(encoding="utf-8"))
REQUEST_INFO = {"action": "request_info", "reason_code": "SOURCE_OF_FUNDS", "investigator_id": "INV-0001"}


async def to_approval(graph):
    config = run_config(ALERT["case_id"])
    await graph.ainvoke(initial_state(ALERT), config)
    return config, await graph.aget_state(config)


async def test_request_info_pauses_for_approval_then_review():
    graph = compile_graph(nodes=stubs.request_info_nodes())
    config, snap = await to_approval(graph)
    assert snap.next == ("approve_info_request",)
    packet = snap.interrupts[0].value
    assert packet["kind"] == "approval" and packet["draft"]["rail"]["passed"]

    await graph.ainvoke(Command(resume={"action": "approve", "approver_id": "INV-0002"}), config)
    snap = await graph.aget_state(config)
    assert snap.next == ("human_review",)
    review = snap.interrupts[0].value
    assert review["recommendation"] == "request_info" and review["info_request"]["status"] == "approved"


async def test_edit_and_reject():
    graph = compile_graph(nodes=stubs.request_info_nodes())
    config, _ = await to_approval(graph)
    await graph.aupdate_state(config, {"pii_vault": {"<PERSON_abc123>": "Jordan Ellis"}})
    edited = {"action": "edit", "approver_id": "INV-0002",
              "message": "Dear <PERSON_abc123>, please help us keep your records up to date."}
    await graph.ainvoke(Command(resume=edited), config)
    request = (await graph.aget_state(config)).values["info_request"]
    assert request["status"] == "approved_with_edits" and request["message"] == edited["message"]
    assert request["outgoing"]["message"].startswith("Dear Jordan Ellis,")  # what the customer receives

    graph = compile_graph(nodes=stubs.request_info_nodes())
    config, _ = await to_approval(graph)
    await graph.ainvoke(Command(resume={"action": "reject", "approver_id": "INV-0002"}), config)
    assert (await graph.aget_state(config)).values["info_request"]["status"] == "rejected"


async def test_tipping_off_edit_is_refused():
    graph = compile_graph(nodes=stubs.request_info_nodes())
    config, _ = await to_approval(graph)
    with pytest.raises(ValueError, match="tip off"):
        await graph.ainvoke(Command(resume={"action": "edit", "approver_id": "INV-2",
                                            "message": "We are investigating suspicious payments."}), config)


async def test_follow_up_state_carries_reply_and_previous_review():
    graph = compile_graph(nodes=stubs.request_info_nodes())
    config, _ = await to_approval(graph)
    await graph.ainvoke(Command(resume={"action": "approve", "approver_id": "INV-2"}), config)
    await graph.ainvoke(Command(resume=REQUEST_INFO), config)
    prior = (await graph.aget_state(config)).values

    state = follow_up_state(prior, ALERT["case_id"], "The 12,500 was from selling my car.", "2026-04-02T10:00:00Z")
    assert follow_up_thread(ALERT["case_id"], state) == "CASE-0002:r1"
    ids = [e["id"] for e in state["evidence"]]
    assert ids == ["reply:CASE-0002-r1", "case:CASE-0002"]
    assert state["follow_up"]["questions"] == ["Please tell us where the funds came from."]
    brief = brief_for("kyc", state)
    assert "FOLLOW-UP ROUND 1" in brief and "selling my car" in brief and "reply:CASE-0002-r1" in brief

    follow = compile_graph(nodes=stubs.nodes())  # this time the evidence supports escalate
    await follow.ainvoke(state, run_config("CASE-0002:r1"))
    snap = await follow.aget_state(run_config("CASE-0002:r1"))
    assert snap.next == ("human_review",) and snap.values["follow_up"]["round"] == 1


def message(value: str, key: str):
    from types import SimpleNamespace
    return SimpleNamespace(key=key.encode(), value=value.encode(), topic="test")


async def test_workers_run_the_whole_request_info_cycle():
    graph = compile_graph(nodes=stubs.request_info_nodes())
    cases, events = FakeCases(), []

    async def publish(event):
        events.append(event)

    case_id = ALERT["case_id"]
    assert await workers.handle_alert(graph, json.dumps(ALERT), publish, cases) == "awaiting_approval"
    approval = {"kind": "approval", "case_id": case_id, "action": "approve", "approver_id": "INV-2"}
    assert await workers.handle_decision(graph, json.dumps(approval), publish, cases) == "awaiting_review"
    # an approval sent again is stale: the case now waits for the disposition
    assert await workers.handle_decision(graph, json.dumps(approval), publish, cases) == "ignored"
    assert await workers.handle_decision(graph, json.dumps({"case_id": case_id, **REQUEST_INFO}),
                                         publish, cases) == "info_requested"

    reply = json.dumps({"case_id": case_id, "reply_text": "The money came from selling my car."})
    assert await workers.handle_followup(graph, reply, publish, cases) == "awaiting_approval"  # stubs ask again
    assert cases.threads[case_id] == "CASE-0002:r1" and cases.replies[(case_id, 1)].startswith("The money")
    types = [e.type for e in events]
    assert {"awaiting_approval", "approval_applied", "decision_applied", "follow_up_started"} <= set(types)
    # the next approval goes to the follow-up thread
    assert await workers.handle_decision(graph, json.dumps(approval), publish, cases) == "awaiting_review"


async def test_reply_for_a_case_not_waiting_is_ignored():
    graph = compile_graph(nodes=stubs.nodes())
    cases, events = FakeCases(), []

    async def publish(event):
        events.append(event)

    reply = json.dumps({"case_id": "CASE-0001", "reply_text": "hello"})
    assert await workers.handle_followup(graph, reply, publish, cases) == "ignored"
    assert events[-1].type == "follow_up_ignored"
