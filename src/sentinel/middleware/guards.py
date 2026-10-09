"""Guardrail middleware for every specialist's tool loop (TDD §10).

Tool-call path (outermost first): OPA authorisation (sees tokens, never PII) -> PII tokens restored in
the arguments -> injection rail on the result -> the tool. Model-call path: every message is
PII-redacted before the model sees it."""

import asyncio

from langchain.agents.middleware import wrap_model_call, wrap_tool_call
from langchain_core.messages import ToolMessage

from sentinel.guardrails.events import record
from sentinel.guardrails.injection import TRUSTED_TOOLS, fence, scan
from sentinel.guardrails.pii import PII_VAULT, redact_content


def redact_messages(messages: list) -> list:
    vault = PII_VAULT.get()
    if vault is None:
        return messages
    return [m.model_copy(update={"content": redact_content(m.content, vault)}) for m in messages]


def pii_redaction(agent: str):
    @wrap_model_call(name=f"PiiRedaction_{agent}")
    async def redact(request, handler):
        # Presidio lookups are blocking HTTP calls: run them off the event loop, which the worker shares
        # with every other case in flight (asyncio.to_thread keeps the PII_VAULT context)
        return await handler(request.override(messages=await asyncio.to_thread(redact_messages, request.messages)))

    return redact


def pii_restore(agent: str):
    @wrap_tool_call(name=f"PiiRestore_{agent}")
    async def restore(request, handler):
        vault = PII_VAULT.get()
        if vault is not None:
            call = request.tool_call
            request = request.override(tool_call={**call, "args": vault.restore(call["args"])})
        return await handler(request)

    return restore


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content or [])


def injection_rail(agent: str):
    @wrap_tool_call(name=f"InjectionRail_{agent}")
    async def rail(request, handler):
        result = await handler(request)
        if request.tool_call["name"] in TRUSTED_TOOLS:
            return result
        if isinstance(result, ToolMessage) and (hits := scan(_text(result.content))):
            record("injection_detected", agent, f"{request.tool_call['name']} returned instruction-like text",
                   tool=request.tool_call["name"], phrases=hits[:5])
            return result.model_copy(update={"content": fence(_text(result.content), hits)})
        return result

    return rail
