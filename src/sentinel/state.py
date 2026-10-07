"""Case state shared by all graph nodes (TDD §5.1)."""

from typing import Annotated, Literal, NotRequired, TypedDict


class Evidence(TypedDict):
    id: str  # "txn:TXN-77812", "acct:ACC-1001", "cust:CUST-00042", ...
    source: str  # tool or node that produced it, e.g. "txn_history.get_transactions"
    agent: str  # agent that collected it
    summary: str


class QAIssue(TypedDict):
    target_agent: Literal["kyc", "txn", "screening", "network", "typology", "narrative"]
    severity: Literal["blocker", "major", "minor"]
    description: str


def merge_evidence(left: list[Evidence], right: list[Evidence]) -> list[Evidence]:
    """Upsert by evidence id so reworked agents replace, not duplicate."""
    merged = {e["id"]: e for e in left or []}
    for e in right or []:
        merged[e["id"]] = e
    return list(merged.values())


def merge_dicts(left: dict, right: dict) -> dict:
    """Each agent writes its own key: {"kyc": {...}}, {"txn": {...}}."""
    return {**(left or {}), **(right or {})}


def add_events(left: list, right: list) -> list:
    """Security events accumulate across agents and rework rounds."""
    return [*(left or []), *(right or [])]


USAGE_KEYS = ("input_tokens", "output_tokens", "model_calls", "tool_calls")


def add_usage(left: dict, right: dict) -> dict:
    """Per-agent token and call counts, summed across rework rounds."""
    merged = dict(left or {})
    for agent, usage in (right or {}).items():
        current = merged.get(agent, {})
        merged[agent] = {k: current.get(k, 0) + usage.get(k, 0) for k in USAGE_KEYS}
    return merged


class CaseState(TypedDict):
    case_id: str
    legal_entity: str
    alert: dict
    tier: NotRequired[Literal["fast", "full"]]
    evidence: Annotated[list[Evidence], merge_evidence]
    findings: Annotated[dict, merge_dicts]
    narrative: NotRequired[dict]  # NarrativeDraft.model_dump()
    qa_issues: NotRequired[list[QAIssue]]  # overwritten each QA round
    qa_rounds: NotRequired[int]
    rework_target: NotRequired[str | None]
    decision: NotRequired[dict | None]  # written ONLY by human_review
    versions: Annotated[dict, merge_dicts]  # prompt/model versions per agent, for audit
    # Guardrails (Slice 6)
    security_events: Annotated[list[dict], add_events]  # injections caught, tools denied, budget hits
    usage: Annotated[dict, add_usage]  # tokens and calls per agent
    pii_vault: Annotated[dict, merge_dicts]  # token -> original value; models only ever see the tokens
    budget_exceeded: NotRequired[bool]
    # Extended human-in-the-loop (Slice 6)
    info_request: NotRequired[dict | None]  # UC-03 customer information request: draft, rail result, approval
    follow_up: NotRequired[dict | None]  # UC-04: round, prior thread, customer reply
