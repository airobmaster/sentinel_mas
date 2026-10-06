"""Smoke test of the Streamlit test console against Bedrock. Run with: SENTINEL_LIVE=1 pytest -m live"""

import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("SENTINEL_LIVE") != "1", reason="set SENTINEL_LIVE=1 to call Bedrock"),
]
APP = Path(__file__).resolve().parents[1] / "devtools" / "streamlit_app.py"


def test_console_runs_a_case_and_records_the_decision():
    at = AppTest.from_file(str(APP), default_timeout=300)
    at.run()
    at.sidebar.selectbox[0].select("CASE-0001 · STRUCT")
    at.sidebar.button[0].click().run()
    assert not at.exception and not at.error
    assert {m.label: m.value for m in at.metric}["Recommendation"] == "escalate"

    next(b for b in at.button if b.label == "Submit decision").click().run()
    assert not at.exception
    assert any("Decision recorded: **escalate**" in s.value for s in at.success)

    # Direct mode has the same Work queue and Event stream views as Kafka mode
    frames = [df.value for df in at.dataframe]
    queue = next(f for f in frames if "status" in f.columns and "case" in f.columns)
    assert queue.iloc[0]["case"] == "CASE-0001" and "escalated" in queue.iloc[0]["status"]
    stream = next(f for f in frames if "event" in f.columns and len(f) > 5)
    assert {"case_started", "node_completed", "awaiting_review", "decision_applied"} <= set(stream["event"])
