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
