"""OPA authorisation for every agent tool call (TDD §9). Deny by default; fails closed."""

import asyncio
import logging
import weakref
from contextvars import ContextVar

import httpx
from langchain.agents.middleware import wrap_tool_call
from langchain_core.messages import ToolMessage

from sentinel.config import settings
from sentinel.guardrails.events import record

log = logging.getLogger("sentinel.security")

# Case context for policy decisions, set per specialist run (see agents/factory.py).
CASE_CONTEXT: ContextVar[dict] = ContextVar("sentinel_case_context", default={})


OPA_ATTEMPTS = 2  # one retry for transient connection errors; still fails closed after that

# One pooled client per event loop: opening a connection per decision costs ~0.5 s through
# Docker Desktop and queues badly under concurrency. Keyed weakly so finished loops drop out.
_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = weakref.WeakKeyDictionary()


def _client() -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None or client.is_closed:
        client = _clients[loop] = httpx.AsyncClient(timeout=httpx.Timeout(5, connect=10))
    return client


async def decide(policy_input: dict) -> tuple[bool, list[str]]:
    """Ask OPA for a decision. Any error after the retry is a deny."""
    error = None
    for _ in range(OPA_ATTEMPTS):
        try:
            resp = await _client().post(
                f"{settings.opa_url}/v1/data/sentinel/tools/decision", json={"input": policy_input}
            )
            resp.raise_for_status()
            result = resp.json().get("result") or {}
            return bool(result.get("allow")), list(result.get("reasons", []))
        except httpx.HTTPError as e:
            error = e
    return False, [f"policy engine unavailable ({type(error).__name__})"]


def opa_authorize(agent: str):
    @wrap_tool_call(name=f"OpaAuthorize_{agent}")
    async def authorize(request, handler):
        call = request.tool_call
        allowed, reasons = await decide(
            {"agent": agent, "tool": call["name"], "args": call["args"], "case": CASE_CONTEXT.get()}
        )
        if not allowed:
            record("tool_denied", agent, f"{call['name']} denied: {'; '.join(reasons) or 'not allowed'}",
                   tool=call["name"], reasons=reasons, args=call.get("args"))  # tokens only, no PII
            return ToolMessage(
                content=f"Denied by policy: {'; '.join(reasons) or 'not allowed'}",
                status="error",
                tool_call_id=call["id"],
                name=call["name"],
            )
        return await handler(request)

    return authorize
