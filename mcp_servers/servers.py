"""FastMCP servers exposing Sentinel's tool functions over Streamable HTTP (TDD §8.1).

Each server wraps the same functions the agents use in-process (sentinel/tools/*), adding:
- service-token auth: a bearer JWT whose `entities` claim must include the requested legal_entity;
- evidence in structured content, so the client can collect exactly what the tool returned.
Record-level entity scoping, argument limits and parameterised queries live in the functions themselves.
"""

import functools
import inspect

import jwt
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.tools.tool import ToolResult
from langchain_core.tools import ToolException

from sentinel.config import MCP_TOKEN_AUDIENCE, settings
from sentinel.tools.case_tools import CASE_FUNCTIONS
from sentinel.tools.kyc_tools import KYC_FUNCTIONS
from sentinel.tools.screening_tools import SCREENING_FUNCTIONS
from sentinel.tools.txn_tools import TXN_FUNCTIONS

SERVERS = {
    "case_mgmt": CASE_FUNCTIONS,
    "kyc_profile": KYC_FUNCTIONS,
    "txn_history": TXN_FUNCTIONS,
    "screening": SCREENING_FUNCTIONS,
}


def caller_entities() -> set[str]:
    """Legal entities the calling service may access, from its bearer token."""
    if not settings.mcp_require_auth:
        return {"*"}
    auth = get_http_headers(include_all=True).get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise ToolError("Missing bearer token")
    try:
        claims = jwt.decode(auth[7:], settings.mcp_dev_secret, algorithms=["HS256"], audience=MCP_TOKEN_AUDIENCE)
    except jwt.PyJWTError as e:
        raise ToolError(f"Invalid token ({type(e).__name__})") from e
    return set(claims.get("entities", []))


def run_tool(func, legal_entity: str, **kwargs) -> ToolResult:
    entities = caller_entities()
    if "*" not in entities and legal_entity not in entities:
        raise ToolError(f"Caller is not authorised for legal entity {legal_entity}")
    try:
        content, evidence = func(legal_entity=legal_entity, **kwargs)
    except ToolException as e:
        raise ToolError(str(e)) from e
    return ToolResult(content=content, structured_content={"evidence": evidence})


def expose(mcp: FastMCP, func) -> None:
    """Register `func` as an MCP tool with the same name, arguments and description."""

    @functools.wraps(func)
    def tool(**kwargs) -> ToolResult:
        return run_tool(func, **kwargs)

    # Advertise the original arguments but no output schema (we return ToolResult ourselves).
    tool.__signature__ = inspect.signature(func).replace(return_annotation=ToolResult)
    tool.__annotations__ = {**func.__annotations__, "return": ToolResult}
    mcp.tool(tool, name=func.__name__)


def build_server(name: str) -> FastMCP:
    mcp = FastMCP(name)
    for func in SERVERS[name]:
        expose(mcp, func)
    return mcp
