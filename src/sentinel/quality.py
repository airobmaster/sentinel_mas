"""Quality gate (FR-141, D6-02/03): outcome metrics against ground truth plus a G-Eval narrative rubric
(DeepEval), checked against thresholds in evals/thresholds.yaml.

    sentinel gate --split holdout --n 6     run the cases here, score them, fail (exit 1) below a threshold
    sentinel gate --cases A,B --from-runs  score cases the workers already ran (nightly eval DAG)

The narrative judge is Claude Haiku on Bedrock: a different model from the one that writes the narratives.
"""

import asyncio
import json
import os
from pathlib import Path

import yaml

from sentinel.config import REPO_ROOT, settings

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")  # no usage data leaves the machine

THRESHOLDS_PATH = REPO_ROOT / "evals" / "thresholds.yaml"
RUBRIC = (
    "You review an anti-money-laundering case narrative written for a human investigator. Score how well it: "
    "(1) states only facts supported by the cited evidence summaries in the context; "
    "(2) explains the red flags, or why there are none, clearly enough for the investigator to decide; "
    "(3) justifies its recommendation from that evidence and the cited policy; "
    "(4) uses neutral, factual language with no speculation about guilt and nothing that would tip off the customer."
)


def thresholds() -> dict:
    return yaml.safe_load(THRESHOLDS_PATH.read_text(encoding="utf-8"))


def judge_model():
    """DeepEval adapter for a Bedrock chat model (structured output when DeepEval asks for a schema)."""
    from deepeval.models.base_model import DeepEvalBaseLLM
    from langchain_aws import ChatBedrockConverse

    class BedrockJudge(DeepEvalBaseLLM):
        def __init__(self, model_id: str):
            self.model_id = model_id
            super().__init__(model_id)

        def load_model(self):
            return ChatBedrockConverse(model=self.model_id, region_name=settings.aws_region, temperature=0,
                                       max_tokens=1024)

        def generate(self, prompt: str, schema=None):
            llm = self.load_model()
            return llm.with_structured_output(schema).invoke(prompt) if schema else llm.invoke(prompt).content

        async def a_generate(self, prompt: str, schema=None):
            llm = self.load_model()
            if schema:
                return await llm.with_structured_output(schema).ainvoke(prompt)
            return (await llm.ainvoke(prompt)).content

        def get_model_name(self) -> str:
            return f"bedrock:{self.model_id}"

    return BedrockJudge(settings.model_qa)


def narrative_metric(threshold: float):
    from deepeval.metrics import GEval
    from deepeval.test_case import LLMTestCaseParams

    return GEval(name="Narrative quality", criteria=RUBRIC, model=judge_model(), threshold=threshold,
                 evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT,
                                    LLMTestCaseParams.CONTEXT], async_mode=True)


async def judge_narratives(rows: list[dict], threshold: float, concurrency: int = 4) -> None:
    """Add `narrative_score` (0-1) and `narrative_reason` to every row that has a narrative."""
    from deepeval.test_case import LLMTestCase

    sem = asyncio.Semaphore(concurrency)

    async def judge(row: dict) -> None:
        if not row.get("narrative_text"):
            return
        case = LLMTestCase(input=row["alert_text"], actual_output=row["narrative_text"],
                           context=row.get("evidence_context") or ["(no evidence)"])
        metric = narrative_metric(threshold)
        async with sem:
            try:
                await metric.a_measure(case, _show_indicator=False)
                row["narrative_score"], row["narrative_reason"] = round(metric.score, 3), metric.reason
            except Exception as e:  # noqa: BLE001 - a judge failure is reported, not fatal
                row["narrative_error"] = f"{type(e).__name__}: {str(e)[:160]}"

    await asyncio.gather(*(judge(r) for r in rows))


def check(metrics: dict, limits: dict) -> list[str]:
    """Threshold failures, e.g. 'escalation_recall 0.83 < 0.90'. Missing metrics (no such cases) are skipped."""
    failures = []
    for name, limit in limits.get("minimum", {}).items():
        value = metrics.get(name)
        if value is not None and value < limit:
            failures.append(f"{name} {value:.3f} < {limit:.3f}")
    for name, limit in limits.get("maximum", {}).items():
        value = metrics.get(name)
        if value is not None and value > limit:
            failures.append(f"{name} {value:.3f} > {limit:.3f}")
    return failures


def finish(report: dict, limits: dict) -> dict:
    """Narrative mean into the metrics, threshold check, report rewritten with the verdict."""
    rows = report["cases"]
    scores = [r["narrative_score"] for r in rows if "narrative_score" in r]
    report["metrics"]["narrative_quality"] = round(sum(scores) / len(scores), 3) if scores else None
    report["metrics"]["narrative_judged"] = len(scores)
    report["gate"] = {"thresholds": limits, "failures": check(report["metrics"], limits)}
    report["gate"]["passed"] = not report["gate"]["failures"]
    Path(report["path"]).write_text(json.dumps({k: v for k, v in report.items() if k != "path"}, indent=2),
                                    encoding="utf-8")
    if settings.data_backend == "postgres":
        from sentinel.persistence import update_eval_run

        update_eval_run(report["run_id"], {**report["metrics"], "gate_passed": report["gate"]["passed"]},
                        report["cases"])
    return report


def gate(case_ids: list[str], split: str, from_runs: bool = False, concurrency: int = 4) -> dict:
    """Run (or read) the cases, score outcomes and narratives, check the thresholds."""
    from sentinel.evaluate import evaluate, score_cases

    limits = thresholds()
    if from_runs:
        report = score_cases(case_ids, source="gate-replay", split=split, details=True)
    else:
        report = asyncio.run(evaluate(case_ids=case_ids, concurrency=concurrency, source="gate", split=split,
                                      details=True))
    asyncio.run(judge_narratives(report["cases"], limits["narrative_case_threshold"], concurrency))
    return finish(report, limits)
