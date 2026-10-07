"""Console with the real full squad on a full-lane mule-ring case (live Bedrock).
Run with: SENTINEL_LIVE=1 pytest -m live tests/test_ui_console_full_squad.py"""

import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from sentinel import data

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("SENTINEL_LIVE") != "1", reason="set SENTINEL_LIVE=1 to call Bedrock"),
]
APP = Path(__file__).resolve().parents[1] / "devtools" / "streamlit_app.py"


def test_full_lane_case_shows_network_typology_and_critic():
    truth = data.ground_truth()
    ring = next(cid for cid, t in sorted(truth.items()) if t["typology"] == "MULE_RING")
    at = AppTest.from_file(str(APP), default_timeout=600)
    at.run()
    at.sidebar.selectbox[0].select(f"{ring} · MULE_RING")
    at.sidebar.button[0].click().run()
    assert not at.exception, at.exception

    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Lane"] == "full" and metrics["Recommendation"] == "escalate"
    assert metrics["Risk score"] != "-" and "critic" in metrics["QA"]
    assert metrics["Assessment"] == "Mule network suspected"  # Network tab
    tables = [df.value for df in at.dataframe]
    assert any("typology" in t.columns and "policy" in t.columns for t in tables)  # Typology tab
    assert any("customer" in t.columns and "relationship" in t.columns and len(t) >= 4 for t in tables)  # Network
    assert any("policy:" in m.value for m in at.markdown)  # policy sections relied on
    assert any("QA critic" in c.value for c in at.caption)
    diagrams = at.get("graphviz_chart")
    assert diagrams and ring_customer_in(diagrams[0])
    header = next(m.value for m in at.markdown if m.value.startswith('<div class="sn-header">'))
    assert "Graph: <b>Neo4j + GDS</b>" in header and "Policy KB: <b>pgvector · 22 sections</b>" in header


def ring_customer_in(element) -> bool:
    return "shape=box" in element.proto.spec and "DEV-R" in element.proto.spec
