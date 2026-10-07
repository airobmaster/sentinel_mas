"""Neo4j graph and the new MCP servers against the running stack.
Needs: docker compose up, `sentinel data load`, `sentinel data kb`, `sentinel data graph`, Neo4j running.
Run with: pytest -m integration"""

import socket

import pytest

from sentinel import data, graphdb
from sentinel.config import settings
from sentinel.tools.mcp_clients import session_tools

pytestmark = pytest.mark.integration


def up(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("localhost", port)) == 0


@pytest.fixture
def postgres_data(monkeypatch):
    monkeypatch.setattr(settings, "data_backend", "postgres")
    data.backend.cache_clear()
    graphdb.network.cache_clear()
    yield
    data.backend.cache_clear()
    graphdb.network.cache_clear()


def ring_customers() -> list[str]:
    truth = data.ground_truth()
    return [a["customer_id"] for a in data.list_alerts() if truth.get(a["case_id"], {}).get("typology") == "MULE_RING"]


@pytest.mark.skipif(not (up(5432) and up(7687) and settings.neo4j_uri), reason="Postgres or Neo4j not available")
def test_neo4j_matches_the_in_memory_graph(postgres_data):
    memory, neo = graphdb.MemoryGraph(), graphdb.Neo4jGraph()
    sample = ring_customers() + ["CUST-00042", "CUST-G0001", "CUST-G0100"]
    neo_scores = neo.customer_scores(sample)
    for cid in sample:
        a, b = memory.scores[cid], neo_scores[cid]
        assert (a["mule_score"], a["component_size"], a["shared_device_peers"], a["fan_in"]) == (
            b["mule_score"], b["component_size"], b["shared_device_peers"], b["fan_in"]), cid
        assert {l["customer_id"] for l in memory.links(cid, 2)} == {l["customer_id"] for l in neo.links(cid, 2)}, cid
        assert [d["device_id"] for d in memory.devices_of(cid)] == [d["device_id"] for d in neo.devices_of(cid)]
    assert all(neo_scores[c]["mule_score"] >= 0.6 for c in ring_customers())


@pytest.mark.skipif(not up(8105), reason="graph_query MCP server not running")
async def test_graph_query_server_over_http(postgres_data):
    ring = ring_customers()[0]
    async with session_tools(["graph_query"], ["get_neighbourhood"]) as [tool]:
        msg = await tool.ainvoke({"type": "tool_call", "id": "1", "name": tool.name,
                                  "args": {"legal_entity": "UK", "customer_id": ring}})
    ids = [e["id"] for e in msg.artifact["structured_content"]["evidence"]]
    assert ids[0] == f"graph:{ring}" and len([i for i in ids if i.startswith("graph:CUST")]) >= 5


@pytest.mark.skipif(not up(8106), reason="policy_kb MCP server not running")
async def test_policy_kb_server_over_http():
    async with session_tools(["policy_kb"], ["search_policy"]) as [tool]:
        msg = await tool.ainvoke({"type": "tool_call", "id": "1", "name": tool.name,
                                  "args": {"legal_entity": "ES", "query": "sanctions true match date of birth"}})
    ids = [e["id"] for e in msg.artifact["structured_content"]["evidence"]]
    assert "policy:AML-ES@2.1#3.6" in ids  # English query finds the Spanish section
