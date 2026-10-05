"""Which tools each agent may use, and how they are loaded (local or MCP, TDD §8.3).

AGENT_TOOLS is the allow-list. It must match guardrails/opa/data.json (a test checks this):
the client only loads these tools, and OPA denies anything else at call time.
"""

from langchain_core.tools import BaseTool

from sentinel.config import settings
from sentinel.tools.case_tools import CASE_TOOLS
from sentinel.tools.kyc_tools import KYC_TOOLS
from sentinel.tools.screening_tools import SCREENING_TOOLS
from sentinel.tools.txn_tools import TXN_TOOLS

AGENT_SERVERS: dict[str, list[str]] = {
    "kyc": ["kyc_profile", "case_mgmt"],
    "txn": ["txn_history"],
    "screening": ["screening"],
    "narrative": [],
}
AGENT_TOOLS: dict[str, list[str]] = {
    "kyc": ["get_customer_profile", "get_crm_notes", "get_case_history"],
    "txn": ["get_transactions", "detect_structuring", "detect_pass_through", "velocity_stats"],
    "screening": ["screen_sanctions_pep", "search_adverse_media"],
    "narrative": [],
}
LOCAL_TOOLS: dict[str, BaseTool] = {t.name: t for t in [*KYC_TOOLS, *CASE_TOOLS, *TXN_TOOLS, *SCREENING_TOOLS]}


async def tools_for(agent: str) -> list[BaseTool]:
    allowed = AGENT_TOOLS.get(agent, [])
    if not allowed:
        return []
    if settings.tool_mode == "local":
        return [LOCAL_TOOLS[name] for name in allowed]
    from sentinel.tools.mcp_clients import mcp_tools

    return await mcp_tools(AGENT_SERVERS[agent], allowed)
