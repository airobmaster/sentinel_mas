"""OPA allow-list consistency and the authorisation middleware (OPA itself is mocked here;
tests/test_integration_stack.py calls the real container)."""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from sentinel.config import REPO_ROOT, settings
from sentinel.middleware import opa
from sentinel.tools.registry import AGENT_TOOLS

CALL = {"name": "close_alert", "args": {"legal_entity": "UK"}, "id": "call-1"}


def test_opa_data_matches_the_python_allow_list():
    opa_data = json.loads((REPO_ROOT / "guardrails" / "opa" / "data.json").read_text(encoding="utf-8"))
    assert opa_data["agent_tools"] == AGENT_TOOLS


async def run_middleware(monkeypatch, decision):
    async def fake_decide(policy_input):
        fake_decide.seen = policy_input
        return decision

    monkeypatch.setattr(opa, "decide", fake_decide)
    handled = []

    async def handler(request):
        handled.append(request)
        return ToolMessage(content="ran", tool_call_id=CALL["id"])

    middleware = opa.opa_authorize("txn")
    opa.CASE_CONTEXT.set({"legal_entity": "UK"})
    result = await middleware.awrap_tool_call(SimpleNamespace(tool_call=CALL), handler)
    return result, handled, fake_decide.seen


async def test_denied_call_never_reaches_the_tool(monkeypatch):
    result, handled, seen = await run_middleware(monkeypatch, (False, ["disposition tools are never available"]))
    assert handled == []
    assert result.status == "error" and "Denied by policy" in result.content
    assert seen == {"agent": "txn", "tool": "close_alert", "args": CALL["args"], "case": {"legal_entity": "UK"}}


async def test_allowed_call_runs_the_tool(monkeypatch):
    result, handled, _ = await run_middleware(monkeypatch, (True, []))
    assert len(handled) == 1 and result.content == "ran"


async def test_unreachable_policy_engine_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "opa_url", "http://127.0.0.1:9")  # nothing listens here
    allowed, reasons = await opa.decide({"agent": "txn", "tool": "get_transactions", "args": {}, "case": {}})
    assert not allowed and "policy engine unavailable" in reasons[0]


@pytest.mark.parametrize("artifact, expected", [
    ([{"id": "txn:A"}], [{"id": "txn:A"}]),
    ({"structured_content": {"evidence": [{"id": "txn:B"}]}}, [{"id": "txn:B"}]),
    (None, []),
])
def test_evidence_from_local_and_mcp_artifacts(artifact, expected):
    from sentinel.agents.factory import artifact_evidence

    assert artifact_evidence(artifact) == expected
