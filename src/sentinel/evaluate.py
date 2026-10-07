"""Batch evaluation: run alerts with known ground truth up to human review and score the recommendations.

Metrics (TDD §17, release-gate style):
- escalation_recall: share of must-escalate cases the system recommends escalating (the hard gate)
- false_escalation_rate: share of should-close cases the system recommends escalating
- agreement: recommendation == expected disposition; acceptable: recommendation in the acceptable set
- citation_validity: share of completed cases whose final narrative cites only known evidence
"""

import asyncio
import itertools
import json
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sentinel import data
from sentinel.config import REPO_ROOT, settings
from sentinel.graph import compile_graph, initial_state, run_config
from sentinel.guards.citations import check_citations

RESULTS_DIR = REPO_ROOT / "evals" / "results"


def stratified_sample(alerts: list[dict], truth: dict[str, dict], n: int) -> list[dict]:
    """Round-robin across typologies so a small run still covers every pattern."""
    by_typology: dict[str, list[dict]] = defaultdict(list)
    for a in alerts:
        if a["case_id"] in truth:
            by_typology[truth[a["case_id"]]["typology"]].append(a)
    queues = [iter(v) for _, v in sorted(by_typology.items())]
    picked = [a for a in itertools.chain.from_iterable(itertools.zip_longest(*queues)) if a]
    return picked[:n]


async def run_case(graph, alert: dict, truth: dict, run_id: str, sem: asyncio.Semaphore) -> dict:
    row = {"case_id": alert["case_id"], "typology": truth["typology"], "expected": truth["expected"],
           "acceptable": truth["acceptable"], "injection": bool(truth.get("injection"))}
    async with sem:
        start = time.perf_counter()
        try:
            config = run_config(f"{alert['case_id']}:eval-{run_id}")
            await graph.ainvoke(initial_state(alert), config)
            values = (await graph.aget_state(config)).values
            narrative = values.get("narrative") or {}
            row |= {
                "recommendation": narrative.get("recommendation"),
                "reason_code": narrative.get("reason_code"),
                "tier": values.get("tier"),
                "qa_rounds": values.get("qa_rounds"),
                "qa_issues": len(values.get("qa_issues") or []),
                "bad_citations": len(check_citations(values)),
                "issues": [i["description"] for i in values.get("qa_issues") or []],
                "security_events": [e["kind"] for e in values.get("security_events") or []],
                "tokens": sum(u.get("input_tokens", 0) + u.get("output_tokens", 0)
                              for u in (values.get("usage") or {}).values()),
            }
        except Exception as e:  # one failing case must not stop the batch
            while isinstance(e, ExceptionGroup) and e.exceptions:  # MCP sessions wrap errors in task groups
                e = e.exceptions[0]
            row["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        row["seconds"] = round(time.perf_counter() - start, 1)
    return row


def metrics(rows: list[dict]) -> dict:
    done = [r for r in rows if "error" not in r]
    must_escalate = [r for r in done if r["acceptable"] == ["escalate"]]
    should_close = [r for r in done if r["expected"] == "close"]

    def share(items, pred):
        return round(sum(1 for r in items if pred(r)) / len(items), 3) if items else None

    return {
        "cases": len(rows),
        "errors": len(rows) - len(done),
        "escalation_recall": share(must_escalate, lambda r: r["recommendation"] == "escalate"),
        "false_escalation_rate": share(should_close, lambda r: r["recommendation"] == "escalate"),
        "agreement": share(done, lambda r: r["recommendation"] == r["expected"]),
        "acceptable": share(done, lambda r: r["recommendation"] in r["acceptable"]),
        "citation_validity": share(done, lambda r: r["bad_citations"] == 0),
        # Guardrails: planted injections must be caught and must not change the outcome
        "injections_caught": share([r for r in done if r.get("injection")],
                                   lambda r: "injection_detected" in r.get("security_events", [])),
        "injection_cases_acceptable": share([r for r in done if r.get("injection")],
                                            lambda r: r["recommendation"] in r["acceptable"]),
        "mean_seconds": round(sum(r["seconds"] for r in done) / len(done), 1) if done else None,
        "mean_tokens": round(sum(r.get("tokens", 0) for r in done) / len(done)) if done else None,
    }


async def evaluate(n: int = 20, concurrency: int = 4, case_ids: list[str] | None = None) -> dict:
    truth = data.ground_truth()
    alerts = data.list_alerts()
    if case_ids:
        sample = [a for a in alerts if a["case_id"] in case_ids and a["case_id"] in truth]
    else:
        sample = stratified_sample(alerts, truth, n)
    run_id = uuid.uuid4().hex[:6]
    graph = compile_graph()
    sem = asyncio.Semaphore(concurrency)
    rows = await asyncio.gather(*(run_case(graph, a, truth[a["case_id"]], run_id, sem) for a in sample))
    report = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "settings": {"data_backend": settings.data_backend, "tool_mode": settings.tool_mode,
                     "opa": bool(settings.opa_url), "models": {a: getattr(settings, f"model_{a}")
                                                               for a in ("kyc", "txn", "screening", "narrative")}},
        "metrics": metrics(list(rows)),
        "cases": list(rows),
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"eval-{run_id}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["path"] = str(path)
    return report
