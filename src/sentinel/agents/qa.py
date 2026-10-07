"""QA node: code checks first, then an independent LLM critic on a different model from the
narrative (Claude Haiku 4.5 by default). The critic only runs when the code checks pass, so a draft
with broken citations is reworked without spending a model call on it."""

from sentinel.agents.briefs import brief_for
from sentinel.agents.factory import get_specialist, run_specialist
from sentinel.config import settings
from sentinel.guards.citations import check_citations, check_completeness
from sentinel.schemas import QAReport
from sentinel.state import CaseState, QAIssue

SEVERITY_ORDER = ("blocker", "major", "minor")
REWORK_SEVERITIES = ("blocker", "major")


def worst_target(issues: list[QAIssue]) -> str | None:
    serious = [i for i in issues if i["severity"] in REWORK_SEVERITIES]
    if not serious:
        return None
    return min(serious, key=lambda i: SEVERITY_ORDER.index(i["severity"]))["target_agent"]


async def critic(state: CaseState) -> tuple[list[QAIssue], dict, dict]:
    """Return (issues, findings update, prompt/model versions)."""
    spec = get_specialist("qa", settings.model_qa, "qa", QAReport)
    report, _ = await run_specialist(spec, brief_for("qa", state), state["legal_entity"])
    issues = [{"target_agent": i.target_agent, "severity": i.severity, "description": f"[critic] {i.description}"}
              for i in report.issues]
    return issues, {"qa": {"passed": report.passed, "checks": report.checks, "model": settings.model_qa,
                           "issues": len(issues)}}, spec.versions


async def qa(state: CaseState) -> dict:
    issues = check_completeness(state) + check_citations(state)
    update: dict = {}
    if settings.qa_llm_critic and not any(i["severity"] in REWORK_SEVERITIES for i in issues):
        critic_issues, findings, versions = await critic(state)
        issues += critic_issues
        update = {"findings": findings, "versions": {"qa": versions}}
    return {
        **update,
        "qa_issues": issues,
        "qa_rounds": state.get("qa_rounds", 0) + 1,
        "rework_target": worst_target(issues),
    }
