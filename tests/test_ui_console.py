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
    at.sidebar.selectbox[0].select("CASE-0001.json")
    at.sidebar.button[0].click().run()
    assert not at.exception and not at.error
    assert {m.label: m.value for m in at.metric}["Recommendation"] == "escalate"

    next(b for b in at.button if b.label == "Submit decision").click().run()
    assert not at.exception
    assert "Decision recorded: **escalate**" in at.success[0].value
