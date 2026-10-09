"""Worker message handling with stubbed agents, an in-memory checkpointer and fake Kafka/DB."""

import json
from types import SimpleNamespace

import pytest

from sentinel import workers
from sentinel.config import REPO_ROOT, settings
from sentinel.graph import compile_graph
from tests import stubs

ALERT = (REPO_ROOT / "data" / "fixtures" / "alerts" / "CASE-0001.json").read_text(encoding="utf-8")
DECISION = json.dumps({"case_id": "CASE-0001", "action": "escalate", "reason_code": "STRUCTURING_CONFIRMED",
                       "investigator_id": "INV-0001"})


class FakeCases:
    def __init__(self):
        self.status, self.threads, self.replies, self.assigned = {}, {}, {}, {}

    async def start(self, alert, thread_id=None):
        self.status[alert["case_id"]] = "in_progress"
        self.threads[alert["case_id"]] = thread_id or alert["case_id"]

    async def set_status(self, case_id, status, tier=None, assigned_role=None, qa_flagged=None):
        self.status[case_id] = status
        if assigned_role:
            self.assigned[case_id] = assigned_role

    async def assign(self, case_id, role):
        self.assigned[case_id] = role

    async def thread_for(self, case_id):
        return self.threads.get(case_id, case_id)

    async def save_reply(self, case_id, round_no, text, received_at):
        self.replies[(case_id, round_no)] = text


@pytest.fixture
def env():
    narrative = stubs.narrative(["txn:TXN-1006"])
    graph = compile_graph(nodes=stubs.nodes(narrative=narrative))
    events = []

    async def publish(event):
        events.append(event)

    return SimpleNamespace(graph=graph, events=events, publish=publish, cases=FakeCases(), narrative=narrative)


def types(env):
    return [e.type for e in env.events]


async def test_alert_runs_to_review(env):
    assert await workers.handle_alert(env.graph, ALERT, env.publish, env.cases) == "awaiting_review"
    assert types(env)[0] == "case_started" and types(env)[-1] == "awaiting_review"
    assert {"triage", "kyc", "txn", "screening", "narrative", "qa"} <= {e.node for e in env.events if e.node}
    assert env.events[-1].data["recommendation"] == "escalate"
    assert env.cases.status["CASE-0001"] == "awaiting_review"


async def test_duplicate_alert_is_ignored(env):
    await workers.handle_alert(env.graph, ALERT, env.publish, env.cases)
    assert await workers.handle_alert(env.graph, ALERT, env.publish, env.cases) == "duplicate"
    assert len(env.narrative.calls) == 1 and types(env)[-1] == "duplicate_ignored"


class ServiceUnavailable(Exception):  # like botocore's errors: retried by the node's RetryPolicy
    pass


async def test_failed_run_resumes_from_its_checkpoint_on_redelivery():
    """A step that fails after its retries leaves the run mid-way; the redelivered alert resumes it
    (completed steps are not run again) instead of being ignored as a duplicate."""
    calls = {"typology": 0}

    async def flaky_typology(state):
        calls["typology"] += 1
        if calls["typology"] <= 3:  # the node's RetryPolicy allows 3 attempts
            raise ServiceUnavailable("Bedrock is unable to process your request")
        return await stubs.typology(state)

    narrative = stubs.narrative(["txn:TXN-1006"])
    graph = compile_graph(nodes=stubs.nodes(typology=flaky_typology, narrative=narrative))
    events, cases = [], FakeCases()

    async def publish(event):
        events.append(event)

    with pytest.raises(ServiceUnavailable):
        await workers.handle_alert(graph, ALERT, publish, cases)
    assert cases.status["CASE-0001"] == "in_progress"
    assert await workers.handle_alert(graph, ALERT, publish, cases) == "awaiting_review"
    kinds = [e.type for e in events]
    assert "case_resumed" in kinds and "duplicate_ignored" not in kinds
    assert [e.node for e in events if e.type == "node_completed"].count("kyc") == 1  # not re-run
    assert calls["typology"] == 4 and len(narrative.calls) == 1
    # Once paused for review, a further redelivery is a real duplicate
    assert await workers.handle_alert(graph, ALERT, publish, cases) == "duplicate"


async def test_qa_degrades_when_the_critic_model_is_unavailable(monkeypatch):
    from botocore.exceptions import ClientError

    from sentinel.agents import qa as qa_module

    async def unavailable(state):
        raise ClientError({"Error": {"Code": "ServiceUnavailableException",
                                     "Message": "Bedrock is unable to process your request."}}, "Converse")

    monkeypatch.setattr(settings, "qa_llm_critic", True)
    monkeypatch.setattr(qa_module, "critic", unavailable)
    monkeypatch.setattr(qa_module, "check_completeness", lambda state: [])
    monkeypatch.setattr(qa_module, "check_citations", lambda state: [])
    out = await qa_module.qa({"usage": {}})
    assert out["rework_target"] is None and out["findings"]["qa"]["passed"] is None
    assert out["qa_issues"][0]["severity"] == "minor" and "[critic unavailable]" in out["qa_issues"][0]["description"]


async def test_decision_resumes_once(env):
    await workers.handle_alert(env.graph, ALERT, env.publish, env.cases)
    assert env.cases.assigned["CASE-0001"] == "l1"  # fast lane: L1 first (BR-08)
    # L1 escalates: the case waits again, now with L2 (BR-16)
    assert await workers.handle_decision(env.graph, DECISION, env.publish, env.cases) == "awaiting_review"
    assert env.cases.assigned["CASE-0001"] == "l2" and types(env)[-2:] == ["decision_applied", "awaiting_review"]
    # L2 escalates: now with the MLRO, who files the SAR
    assert await workers.handle_decision(env.graph, DECISION, env.publish, env.cases) == "awaiting_review"
    assert env.cases.assigned["CASE-0001"] == "mlro"
    assert await workers.handle_decision(env.graph, DECISION, env.publish, env.cases) == "ignored"  # not an MLRO action
    assert "invalid decision" in env.events[-1].detail
    sar = json.dumps({"case_id": "CASE-0001", "action": "file_sar", "reason_code": "SUSPICION_CONFIRMED",
                      "investigator_id": "MLRO-1"})
    assert await workers.handle_decision(env.graph, sar, env.publish, env.cases) == "sar_filed"
    assert env.cases.status["CASE-0001"] == "sar_filed"
    assert await workers.handle_decision(env.graph, sar, env.publish, env.cases) == "ignored"
    assert env.events[-1].detail == "case is not waiting for review"


async def test_decision_for_unknown_case_is_ignored(env):
    assert await workers.handle_decision(env.graph, DECISION, env.publish, env.cases) == "ignored"
    assert env.events[-1].detail == "unknown case"


async def test_invalid_decision_leaves_case_waiting(env):
    await workers.handle_alert(env.graph, ALERT, env.publish, env.cases)
    bad = json.dumps({**json.loads(DECISION), "reason_code": "FP_DATA_ERROR"})
    assert await workers.handle_decision(env.graph, bad, env.publish, env.cases) == "ignored"
    assert "invalid decision" in env.events[-1].detail
    from sentinel.graph import run_config
    assert (await env.graph.aget_state(run_config("CASE-0001"))).next == ("human_review",)


def message(value: str, key: str = "CASE-0001"):
    return SimpleNamespace(key=key.encode(), value=value.encode(), topic="aml.alerts.v1")


async def test_malformed_message_goes_to_dlq_without_retry(env):
    dlq = []

    async def send_dlq(*args):
        dlq.append(args)

    calls = []

    async def handler(graph, raw, publish, cases):
        calls.append(raw)
        return await workers.handle_alert(graph, raw, publish, cases)

    await workers.process_with_retries(handler, env.graph, message("{}"), env.publish, env.cases, send_dlq)
    assert len(calls) == 1 and len(dlq) == 1 and "invalid message" in dlq[0][3]
    assert types(env) == ["error"] and env.cases.status["CASE-0001"] == "error"


async def test_failures_are_retried_then_dead_lettered(env, monkeypatch):
    monkeypatch.setattr(settings, "worker_max_attempts", 3)
    dlq, calls = [], []

    async def send_dlq(*args):
        dlq.append(args)

    async def flaky(graph, raw, publish, cases):
        calls.append(1)
        raise TimeoutError("model timed out")

    await workers.process_with_retries(flaky, env.graph, message(ALERT), env.publish, env.cases, send_dlq)
    assert len(calls) == 3 and dlq[0][0] == "aml.alerts.v1" and "TimeoutError" in dlq[0][3]


def test_batch_grouped_by_case_in_order():
    msgs = [SimpleNamespace(key=k) for k in (b"A", b"B", b"A")]
    groups = workers.by_case(msgs)
    assert [[m.key for m in g] for g in groups] == [[b"A", b"A"], [b"B"]]
