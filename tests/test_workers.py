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
        self.status = {}

    async def start(self, alert):
        self.status[alert["case_id"]] = "in_progress"

    async def set_status(self, case_id, status):
        self.status[case_id] = status


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


async def test_decision_resumes_once(env):
    await workers.handle_alert(env.graph, ALERT, env.publish, env.cases)
    assert await workers.handle_decision(env.graph, DECISION, env.publish, env.cases) == "escalated"
    assert env.cases.status["CASE-0001"] == "escalated" and types(env)[-1] == "decision_applied"
    assert await workers.handle_decision(env.graph, DECISION, env.publish, env.cases) == "ignored"
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
