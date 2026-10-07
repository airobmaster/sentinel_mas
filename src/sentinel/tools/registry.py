"""Which tools each agent may use, and how they are loaded (local or MCP, TDD §8.3).

AGENT_TOOLS is the allow-list. It must match guardrails/opa/data.json (a test checks this):
the client only loads these tools, and OPA denies anything else at call time.
"""

from contextlib import asynccontextmanager

from langchain_core.tools import BaseTool

from sentinel.config import settings
from sentinel.tools.case_tools import CASE_TOOLS
from sentinel.tools.graph_tools import GRAPH_TOOLS
from sentinel.tools.kyc_tools import KYC_TOOLS
from sentinel.tools.policy_tools import POLICY_TOOLS
from sentinel.tools.screening_tools import SCREENING_TOOLS
from sentinel.tools.txn_tools import TXN_TOOLS

AGENT_SERVERS: dict[str, list[str]] = {
    "kyc": ["kyc_profile", "case_mgmt"],
    "txn": ["txn_history"],
    "screening": ["screening"],
    "network": ["graph_query"],
    "typology": ["policy_kb"],
    "narrative": [],
    "qa": [],
}
AGENT_TOOLS: dict[str, list[str]] = {
    "kyc": ["get_customer_profile", "get_crm_notes", "get_case_history"],
    "txn": ["get_transactions", "detect_structuring", "detect_pass_through", "velocity_stats"],
    "screening": ["screen_sanctions_pep", "search_adverse_media"],
    "network": ["get_neighbourhood", "get_shared_devices", "get_community_scores"],
    "typology": ["search_policy", "get_typology"],
    "narrative": [],
    "qa": [],
}
LOCAL_TOOLS: dict[str, BaseTool] = {
    t.name: t for t in [*KYC_TOOLS, *CASE_TOOLS, *TXN_TOOLS, *SCREENING_TOOLS, *GRAPH_TOOLS, *POLICY_TOOLS]
}


def local_tools_for(agent: str) -> list[BaseTool]:
    return [LOCAL_TOOLS[name] for name in AGENT_TOOLS.get(agent, [])]


@asynccontextmanager
async def tools_for(agent: str):
    """Yield the agent's tools for one run: in-process, or bound to open MCP sessions."""
    if settings.tool_mode == "local" or not AGENT_TOOLS.get(agent):
        yield local_tools_for(agent)
        return
    from sentinel.tools.mcp_clients import session_tools

    async with session_tools(AGENT_SERVERS[agent], AGENT_TOOLS[agent]) as tools:
        yield tools
