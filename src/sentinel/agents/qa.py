"""QA node. Slice 1 runs the code checks only; the LLM critic (model != narrative) comes later."""

from sentinel.guards.citations import check_citations, check_completeness
from sentinel.state import CaseState, QAIssue

SEVERITY_ORDER = ("blocker", "major", "minor")


def worst_target(issues: list[QAIssue]) -> str | None:
    if not issues:
        return None
    return min(issues, key=lambda i: SEVERITY_ORDER.index(i["severity"]))["target_agent"]


async def qa(state: CaseState) -> dict:
    issues = check_completeness(state) + check_citations(state)
    return {
        "qa_issues": issues,
        "qa_rounds": state.get("qa_rounds", 0) + 1,
        "rework_target": worst_target(issues),
    }
