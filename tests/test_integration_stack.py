"""Against the Docker stack (docker compose up -d --build; sentinel data generate; sentinel data load).
Run with: pytest -m integration. Skips when the services are not reachable."""

import socket

import jwt
import pytest
from langchain_mcp_adapters.client import MultiServerMCPClient

from sentinel import data
from sentinel.config import REPO_ROOT, settings
from sentinel.repo.json_repo import JsonRepo
from sentinel.repo.postgres_repo import PostgresRepo
from sentinel.tools.mcp_clients import mcp_tools, service_token

pytestmark = pytest.mark.integration
OPA_URL = "http://localhost:8181"
GENERATED = REPO_ROOT / "data" / "generated" / "dataset.json"


def reachable(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("localhost", port)) == 0


needs_pg = pytest.mark.skipif(not reachable(5432), reason="Postgres not running")
needs_opa = pytest.mark.skipif(not reachable(8181), reason="OPA not running")
needs_mcp = pytest.mark.skipif(not reachable(8103), reason="MCP servers not running")


@needs_pg
def test_postgres_matches_json_backend():
    if not GENERATED.exists():
        pytest.skip("run `sentinel data generate` and `sentinel data load` first")
    js = JsonRepo([REPO_ROOT / "data" / "fixtures" / "cases.json", GENERATED])
    pg = PostgresRepo("postgresql://sentinel:sentinel@localhost:5432/sentinel")
    assert len(pg.list_alerts()) == len(js.list_alerts())
    assert pg.ground_truth() == js.ground_truth()
    for alert in js.list_alerts()[::7]:
        cid, acct = alert["customer_id"], alert["account_ids"][0]
        assert pg.get_alert(alert["case_id"]) == alert
        assert pg.get_customer(cid) == js.get_customer(cid)
        assert pg.account_entity(acct) == js.account_entity(acct)
        assert pg.crm_notes_for(cid) == js.crm_notes_for(cid)
        assert pg.case_history_for(cid) == js.case_history_for(cid)
        assert pg.transactions_for(acct, "2026-03-31", 90) == js.transactions_for(acct, "2026-03-31", 90)
    assert sorted(pg.watchlist_entries(), key=lambda e: e["entry_id"]) == sorted(
        js.watchlist_entries(), key=lambda e: e["entry_id"])


@needs_opa
@pytest.mark.parametrize("policy_input, allowed", [
    ({"agent": "txn", "tool": "get_transactions", "args": {"legal_entity": "UK"}, "case": {"legal_entity": "UK"}}, True),
    ({"agent": "txn", "tool": "close_alert", "args": {"legal_entity": "UK"}, "case": {"legal_entity": "UK"}}, False),
    ({"agent": "screening", "tool": "get_transactions", "args": {"legal_entity": "UK"}, "case": {"legal_entity": "UK"}}, False),
    ({"agent": "kyc", "tool": "get_crm_notes", "args": {"legal_entity": "ES"}, "case": {"legal_entity": "UK"}}, False),
])
async def test_opa_decisions(monkeypatch, policy_input, allowed):
    from sentinel.middleware.opa import decide

    monkeypatch.setattr(settings, "opa_url", OPA_URL)
    ok, reasons = await decide(policy_input)
    assert ok is allowed, reasons


@needs_mcp
async def test_mcp_tools_over_http_return_evidence(monkeypatch):
    monkeypatch.setattr(settings, "mcp_urls", {**settings.mcp_urls, "txn_history": "http://localhost:8103/mcp/"})
    tools = await mcp_tools(["txn_history"], ["detect_structuring"])
    assert [t.name for t in tools] == ["detect_structuring"]
    msg = await tools[0].ainvoke({"type": "tool_call", "id": "1", "name": "detect_structuring",
                                  "args": {"legal_entity": "UK", "account_id": "ACC-1001", "as_of": "2026-03-31"}})
    assert msg.artifact["structured_content"]["evidence"][0]["id"] == "txn:TXN-1006"


@needs_mcp
@pytest.mark.parametrize("token, error", [
    (jwt.encode({"aud": "sentinel-mcp", "entities": ["UK"]}, "wrong-key-of-sufficient-length-12345", algorithm="HS256"),
     "Invalid token"),
    (None, "not authorised for legal entity UK"),  # valid token for ES only
])
async def test_mcp_rejects_bad_or_out_of_scope_tokens(token, error):
    token = token or service_token(entities=("ES",))
    client = MultiServerMCPClient({"txn": {"url": "http://localhost:8103/mcp/", "transport": "streamable_http",
                                           "headers": {"Authorization": f"Bearer {token}"}}})
    tool = next(t for t in await client.get_tools() if t.name == "get_transactions")
    msg = await tool.ainvoke({"type": "tool_call", "id": "1", "name": tool.name,
                              "args": {"legal_entity": "UK", "account_id": "ACC-1001", "as_of": "2026-03-31"}})
    assert msg.status == "error" and error in str(msg.content)


def test_backend_setting_is_respected():
    assert data.backend().__class__.__name__ == ("PostgresRepo" if settings.data_backend == "postgres" else "JsonRepo")
