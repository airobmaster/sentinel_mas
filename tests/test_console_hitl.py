"""Console (Direct mode) with stubbed agents: UC-03 approval dialog, UC-04 customer reply and the
follow-up run, and the Security tab. No Bedrock calls."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from sentinel import graph as graph_module
from tests import stubs

APP = Path(__file__).resolve().parents[1] / "devtools" / "streamlit_app.py"


@pytest.fixture
def stubbed_graph(monkeypatch):
    original = graph_module.compile_graph
    monkeypatch.setattr(graph_module, "compile_graph",
                        lambda checkpointer=None, nodes=None: original(checkpointer, stubs.request_info_nodes()))


def button(at, label):
    return next(b for b in at.button if b.label == label)


def test_approval_then_request_info_then_customer_reply(stubbed_graph):
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    at.sidebar.selectbox[0].select("CASE-0002 · BENIGN_ONE_OFF")
    at.sidebar.button[0].click().run()
    assert not at.exception, at.exception

    # UC-03: the run pauses for approval of the customer information request
    assert any("approval needed" in m.value for m in at.markdown)
    assert any("Tipping-off rail passed" in s.value for s in at.success)
    button(at, "Approve").click().run()
    assert not at.exception, at.exception

    # Disposition: request info (the recommendation); the summary shows how long the run took
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Recommendation"] == "request_info" and metrics["Investigation time"].endswith("s")
    assert any("Time and usage by agent" in m.value for m in at.markdown)
    button(at, "Submit decision").click().run()
    assert any("Decision recorded: **request_info**" in s.value for s in at.success)

    # UC-04: the customer replies -> follow-up run with the reply as evidence
    at.text_area(key="reply-CASE-0002").input("The money came from selling my car.").run()
    button(at, "Send customer reply").click().run()
    assert not at.exception, at.exception
    assert any("Follow-up round 1" in i.value and "selling my car" in i.value for i in at.info)
    queue = next(df.value for df in at.dataframe if "status" in df.value.columns and "case" in df.value.columns)
    assert "awaiting_approval" in queue.iloc[0]["status"]  # the stubs ask for information again
    # The Event stream tab keeps every run; the case's Trace tab shows only the follow-up run by default
    stream = max((df.value for df in at.dataframe if "event" in df.value.columns), key=len)
    assert {"awaiting_approval", "approval_applied", "decision_applied", "follow_up_started"} <= set(stream["event"])

    # Security tab: metrics render; the probe explains that OPA is off in offline tests
    assert {"Injections caught", "Tool calls denied", "Tokens used", "PII values redacted"} <= {m.label for m in at.metric}
    button(at, "Run red-team probe").click().run()
    assert any("OPA is not configured" in w.value for w in at.warning)
