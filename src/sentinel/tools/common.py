"""Shared helpers for tool implementations.

Tool functions are plain Python returning (content for the model, evidence list). The same
functions run in-process (wrapped by `local_tools`) or behind MCP servers (`mcp_servers/`).
Every tool takes `legal_entity` and checks the requested record belongs to it (TDD §8.1).
"""

import hashlib

from langchain_core.tools import BaseTool, StructuredTool, ToolException

from sentinel import data

MAX_LOOKBACK_DAYS = 400  # TDD §8.1 hard limit on date ranges


def render(evidence: list[dict]) -> str:
    return "\n".join(f"{e['id']}: {e['summary']}" for e in evidence)


def opaque(*parts: str) -> str:
    """Short stable hash, so evidence IDs never carry names or other PII."""
    return hashlib.sha256("|".join(parts).lower().encode()).hexdigest()[:10]


def nothing_found(kind: str, subject: str, source: str, summary: str) -> tuple[str, list[dict]]:
    """A check that found nothing is still evidence: the narrative can cite that it was done."""
    evidence = [{"id": f"check:{kind}:{subject}", "source": source, "summary": summary}]
    return render(evidence), evidence


def check_lookback(lookback_days: int) -> None:
    if not 1 <= lookback_days <= MAX_LOOKBACK_DAYS:
        raise ToolException(f"lookback_days must be between 1 and {MAX_LOOKBACK_DAYS}")


def check_customer(legal_entity: str, customer_id: str) -> dict:
    customer = data.get_customer(customer_id)
    if not customer or customer["legal_entity"] != legal_entity:
        raise ToolException(f"Customer {customer_id} not found in legal entity {legal_entity}")
    return customer


def check_account(legal_entity: str, account_id: str) -> None:
    if data.account_entity(account_id) != legal_entity:
        raise ToolException(f"Account {account_id} not found in legal entity {legal_entity}")


def local_tools(*funcs) -> list[BaseTool]:
    """Wrap tool functions for in-process use; errors go back to the model as tool messages."""
    return [
        StructuredTool.from_function(f, response_format="content_and_artifact", handle_tool_error=True)
        for f in funcs
    ]
