"""MCP tools over Streamable HTTP (TDD §8.3), with one session per server for the life of a run."""

import time
from contextlib import AsyncExitStack, asynccontextmanager

import jwt
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from sentinel.config import MCP_TOKEN_AUDIENCE, settings

TOKEN_TTL_SECONDS = 12 * 3600


def service_token(entities: tuple[str, ...] = ("UK", "ES")) -> str:
    """Dev service token (HS256, shared secret). Production uses the bank IdP (TDD §8.1 [REF])."""
    now = int(time.time())
    claims = {"sub": "sentinel-agents", "aud": MCP_TOKEN_AUDIENCE, "entities": list(entities),
              "iat": now, "exp": now + TOKEN_TTL_SECONDS}
    return jwt.encode(claims, settings.mcp_dev_secret, algorithm="HS256")


def client_for(servers: list[str]) -> MultiServerMCPClient:
    token = service_token()
    return MultiServerMCPClient(
        {
            s: {"url": settings.mcp_urls[s], "transport": "streamable_http",
                "headers": {"Authorization": f"Bearer {token}"}}
            for s in servers
        }
    )


@asynccontextmanager
async def session_tools(servers: list[str], allowed: list[str]):
    """Yield the allowed tools bound to one open session per server. Without this, the adapter
    opens a new session (and connection) for every tool call, which is slow under load.
    The adapter returns server-side errors to the model as error ToolMessages."""
    client = client_for(servers)
    async with AsyncExitStack() as stack:
        tools: list[BaseTool] = []
        for server in servers:
            session = await stack.enter_async_context(client.session(server))
            tools += await load_mcp_tools(session)
        yield [t for t in tools if t.name in allowed]
