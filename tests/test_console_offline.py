"""The console renders in both modes without Bedrock (no investigation is run here)."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "devtools" / "streamlit_app.py"


def test_direct_mode_renders_with_three_views():
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    assert [t.label for t in at.tabs][:3] == ["Case", "Work queue", "Event stream"]
    header = next(m.value for m in at.markdown if m.value.startswith('<div class="sn-header">'))  # branded header
    # Markdown shows lines indented by 4+ spaces as a code block instead of rendering the HTML
    assert not any(line.startswith("    ") for line in header.splitlines())
    assert "Graph: <b>in-memory</b>" in header and "Policy KB: <b>keyword · 22 sections</b>" in header
    assert any("Not run yet" in i.value for i in at.info)
    assert at.sidebar.button[0].label == "▶ Run investigation"


def test_kafka_mode_without_stack_explains_what_to_start(monkeypatch):
    from sentinel.config import settings

    monkeypatch.setattr(settings, "kafka_bootstrap", "localhost:1")  # nothing listens here
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    at.sidebar.radio[0].set_value("Kafka (full stack)").run()
    assert not at.exception, at.exception
    assert any("not reachable" in e.value for e in at.error)
