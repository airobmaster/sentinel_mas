"""The API service and the console's API mode against the Docker stack (`docker compose up -d api`),
with the configured sign-in (Cognito test users, or dev tokens). Read-only except for a QA label on
a decided case, which is removed afterwards. Run with: pytest -m integration"""

import socket
from pathlib import Path

import httpx
import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from sentinel.api.auth import token_for
from sentinel.config import settings

APP = Path(__file__).resolve().parents[1] / "devtools" / "streamlit_app.py"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(socket.socket().connect_ex(("localhost", 8000)) != 0, reason="API not running"),
]


def call(method: str, path: str, role: str, **kwargs) -> httpx.Response:
    return httpx.request(method, f"{settings.api_url}{path}", timeout=30,
                         headers={"Authorization": f"Bearer {token_for(role)}"}, **kwargs)


def test_api_service_roles_and_reads():
    assert httpx.get(f"{settings.api_url}/readyz").json() == {"postgres": True, "kafka": True}
    assert httpx.get(f"{settings.api_url}/cases").status_code == 401
    assert call("GET", "/me", "qa").json()["roles"] == ["qa"]
    queue = call("GET", "/cases", "l1").json()
    assert queue and {"case_id", "status", "tier"} <= set(queue[0])
    assert call("GET", "/qa/sample", "l1").status_code == 403
    assert call("POST", "/cases", "l1", json={}).status_code in (403, 422)


def test_console_api_mode_case_view_and_qa_label():
    decided = [r for r in call("GET", "/cases", "qa", params={"status": ["escalated", "closed"]}).json()]
    if not decided:
        pytest.skip("no decided case to review")
    case_id = decided[0]["case_id"]
    with psycopg.connect(settings.pg_dsn) as conn:  # the reviewer starts with an unlabelled sample
        conn.execute("DELETE FROM cases.qa_labels WHERE reviewer = 'qa.reviewer'")
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.session_state["case"] = case_id
    at.run()
    at.sidebar.radio[0].set_value("API (full stack)").run()
    assert not at.exception, at.exception
    assert any(m.label == "Recommendation" for m in at.metric)  # the decided case renders from GET /cases/{id}

    at.sidebar.selectbox(key="api_role").set_value("qa").run()  # QA reviewer: sample + rubric
    assert not at.exception, at.exception
    try:
        next(b for b in at.button if b.label == "Save label").click().run()
        assert any("Label saved" in s.value for s in at.success)
    finally:
        with psycopg.connect(settings.pg_dsn) as conn:
            conn.execute("DELETE FROM cases.qa_labels WHERE reviewer = 'qa.reviewer'")
