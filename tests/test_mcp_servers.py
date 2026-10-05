"""MCP servers in-memory (no HTTP): tool surface, evidence in structured content, errors and auth."""

import pytest
from fastmcp import Client

from mcp_servers.servers import SERVERS, build_server
from sentinel.config import settings
from sentinel.tools.registry import AGENT_SERVERS, AGENT_TOOLS

ARGS = {"legal_entity": "UK", "account_id": "ACC-1001", "as_of": "2026-03-31"}


@pytest.fixture
def no_auth(monkeypatch):
    monkeypatch.setattr(settings, "mcp_require_auth", False)


async def test_servers_expose_every_allowed_tool(no_auth):
    exposed = {}
    for name in SERVERS:
        async with Client(build_server(name)) as client:
            exposed[name] = {t.name for t in await client.list_tools()}
    for agent, tools in AGENT_TOOLS.items():
        available = set().union(*(exposed[s] for s in AGENT_SERVERS[agent])) if AGENT_SERVERS[agent] else set()
        assert set(tools) <= available, agent


async def test_tool_returns_text_and_structured_evidence(no_auth):
    async with Client(build_server("txn_history")) as client:
        tool = next(t for t in await client.list_tools() if t.name == "detect_structuring")
        assert "legal_entity" in tool.inputSchema["required"] and tool.outputSchema is None
        result = await client.call_tool("detect_structuring", ARGS)
    assert result.content[0].text.startswith("STRUCTURING PATTERN")
    assert [e["id"] for e in result.structured_content["evidence"]][0] == "txn:TXN-1006"


async def test_record_outside_entity_is_an_error(no_auth):
    async with Client(build_server("txn_history")) as client:
        result = await client.call_tool("get_transactions", {**ARGS, "legal_entity": "ES"}, raise_on_error=False)
    assert result.is_error and "not found in legal entity ES" in result.content[0].text


async def test_calls_without_a_token_are_rejected():
    assert settings.mcp_require_auth
    async with Client(build_server("kyc_profile")) as client:
        result = await client.call_tool(
            "get_customer_profile", {"legal_entity": "UK", "customer_id": "CUST-00042"}, raise_on_error=False
        )
    assert result.is_error and "Missing bearer token" in result.content[0].text
