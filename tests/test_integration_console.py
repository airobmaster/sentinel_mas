"""Console Kafka mode end to end, deterministic: a worker with stubbed agents runs in a background
thread (no Bedrock), and the console is driven with Streamlit's AppTest.
Needs the Docker stack, loaded data and topics. Run with: pytest -m integration"""

import asyncio
import socket
import threading
import time
from pathlib import Path

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from sentinel import data
from sentinel.config import settings
from sentinel.graph import compile_graph
from sentinel.persistence import durable_state, reset_case
from sentinel.workers import HANDLERS, run_worker
from tests import stubs
from tests.kafka_guard import worker_running

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(any(socket.socket().connect_ex(("localhost", p)) for p in (5432, 9092)),
                       reason="Postgres or Kafka not running"),
    pytest.mark.skipif(worker_running(), reason="a Sentinel worker is running and would take the test's messages"),
]
APP = Path(__file__).resolve().parents[1] / "devtools" / "streamlit_app.py"
CASE = "CASE-ITC-0001"  # a temporary copy of CASE-0001: real cases keep their runs and event history


@pytest.fixture
def test_case():
    with psycopg.connect(settings.pg_dsn, autocommit=True) as conn:
        conn.execute("""INSERT INTO cases.alerts (case_id, legal_entity, customer_id, alert, expected, status)
                        SELECT %s, legal_entity, customer_id, alert || jsonb_build_object('case_id', %s::text),
                               expected, 'new' FROM cases.alerts WHERE case_id = 'CASE-0001'
                        ON CONFLICT (case_id) DO NOTHING""", (CASE, CASE))
    data.backend.cache_clear()
    yield
    reset_case(CASE)
    with psycopg.connect(settings.pg_dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM cases.alerts WHERE case_id = %s", (CASE,))


@pytest.fixture
def stub_worker(test_case):
    stop = threading.Event()

    async def main():
        stop_async = asyncio.Event()
        async with durable_state() as (checkpointer, cases):
            graph = compile_graph(checkpointer=checkpointer, nodes=stubs.nodes())
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
    at.sidebar.selectbox[0].select(next(o for o in at.sidebar.selectbox[0].options if o.startswith(CASE))).run()
    next(b for b in at.sidebar.button if "Publish alert" in b.label).click().run()
    assert not at.exception, at.exception

    wait_until(at, lambda a: any(b.label == "Submit decision" for b in a.button))
    assert {m.label: m.value for m in at.metric}["Recommendation"] == "escalate"

    next(b for b in at.button if b.label == "Submit decision").click().run()
    wait_until(at, lambda a: any("Decision recorded: **escalate**" in s.value for s in a.success))
    assert any("escalated" in m.value for m in at.markdown)
    case_events = [df.value for df in at.dataframe if "event" in df.value.columns and (df.value["case"] == CASE).any()]
    assert case_events and {"case_started", "awaiting_review", "decision_applied"} <= set(case_events[0]["event"])
