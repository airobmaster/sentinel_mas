"""Load an agent's tools from the MCP servers over Streamable HTTP (TDD §8.3)."""

import time

import jwt
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from sentinel.config import MCP_TOKEN_AUDIENCE, settings

TOKEN_TTL_SECONDS = 12 * 3600


def service_token(entities: tuple[str, ...] = ("UK", "ES")) -> str:
    """Dev service token (HS256, shared secret). Production uses the bank IdP (TDD §8.1 [REF])."""
    now = int(time.time())
    claims = {"sub": "sentinel-agents", "aud": MCP_TOKEN_AUDIENCE, "entities": list(entities),
              "iat": now, "exp": now + TOKEN_TTL_SECONDS}
    return jwt.encode(claims, settings.mcp_dev_secret, algorithm="HS256")


async def mcp_tools(servers: list[str], allowed: list[str]) -> list[BaseTool]:
    token = service_token()
    client = MultiServerMCPClient(
        {
            s: {"url": settings.mcp_urls[s], "transport": "streamable_http",
                "headers": {"Authorization": f"Bearer {token}"}}
            for s in servers
        }
    )
    # The adapter already returns server-side errors to the model as error ToolMessages.
    return [t for t in await client.get_tools() if t.name in allowed]
