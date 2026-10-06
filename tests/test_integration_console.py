"""Console Kafka mode end to end, deterministic: a worker with stubbed agents runs in a background
thread (no Bedrock), and the console is driven with Streamlit's AppTest.
Needs the Docker stack, loaded data and topics. Run with: pytest -m integration"""

import asyncio
import socket
import threading
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from sentinel.graph import compile_graph
from sentinel.persistence import durable_state, reset_case
from sentinel.workers import HANDLERS, run_worker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(any(socket.socket().connect_ex(("localhost", p)) for p in (5432, 9092)),
                       reason="Postgres or Kafka not running"),
]
APP = Path(__file__).resolve().parents[1] / "devtools" / "streamlit_app.py"
CASE = "CASE-0001"


def stub(agent: str, eid: str):
    async def node(state):
        return {"evidence": [{"id": eid, "source": "test", "agent": agent, "summary": "stub evidence"}],
                "findings": {agent: {"stub": True}}}
    return node


async def narrative(state):
    return {"narrative": {"summary": "Stubbed narrative.", "claims": [{"text": "c", "evidence_ids": ["txn:T1"]}],
                          "recommendation": "escalate", "reason_code": "STRUCTURING_CONFIRMED", "open_questions": []}}


@pytest.fixture
def stub_worker():
    stop = threading.Event()

    async def main():
        stop_async = asyncio.Event()
        async with durable_state() as (checkpointer, cases):
            graph = compile_graph(checkpointer=checkpointer, nodes={
                "kyc": stub("kyc", "crm:N1"), "txn": stub("txn", "txn:T1"),
                "screening": stub("screening", "list:X"), "narrative": narrative})
            task = asyncio.create_task(run_worker(graph, cases, HANDLERS["all"], 2, stop_async))
            while not stop.is_set():
                await asyncio.sleep(0.2)
            stop_async.set()
            await task

    loop = asyncio.SelectorEventLoop()
    thread = threading.Thread(target=loop.run_until_complete, args=(main(),), daemon=True)
    reset_case(CASE)
    thread.start()
    time.sleep(3)  # let the consumers join their groups
    yield
    stop.set()
    thread.join(20)
    reset_case(CASE)


def wait_until(at: AppTest, predicate, timeout: float = 90) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        at.run()
        assert not at.exception, at.exception
        if predicate(at):
            return
        time.sleep(2)
    raise AssertionError("console never reached the expected state")


def test_console_kafka_mode_publish_review_decide(stub_worker):
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    at.sidebar.radio[0].set_value("Kafka (full stack)").run()
    at.sidebar.selectbox[0].select(f"{CASE} · STRUCT").run()
    next(b for b in at.sidebar.button if "Publish alert" in b.label).click().run()
    assert not at.exception, at.exception

    wait_until(at, lambda a: any(b.label == "Submit decision" for b in a.button))
    assert {m.label: m.value for m in at.metric}["Recommendation"] == "escalate"

    next(b for b in at.button if b.label == "Submit decision").click().run()
    wait_until(at, lambda a: any("Decision recorded: **escalate**" in s.value for s in a.success))
    assert any("escalated" in m.value for m in at.markdown)
    case_events = [df.value for df in at.dataframe if "event" in df.value.columns and (df.value["case"] == CASE).any()]
    assert case_events and {"case_started", "awaiting_review", "decision_applied"} <= set(case_events[0]["event"])
