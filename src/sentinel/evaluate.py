"""Batch evaluation: run alerts with known ground truth up to human review and score the recommendations.

Metrics (TDD §17, release-gate style):
- escalation_recall: share of must-escalate cases the system recommends escalating (the hard gate)
- false_escalation_rate: share of should-close cases the system recommends escalating
- agreement: recommendation == expected disposition; acceptable: recommendation in the acceptable set
- citation_validity: share of citations (narrative claims and typology references) that point to known
  evidence; an uncited claim counts as a bad citation. Per citation, so one slip in a small sample does not
  dominate the score
"""

import asyncio
import itertools
import json
import random
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime

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
    # Alternate must-escalate and other typologies, so even a small sample (the CI gate) has both kinds
    escalate = [k for k in sorted(by_typology) if truth[by_typology[k][0]["case_id"]]["expected"] == "escalate"]
    others = [k for k in sorted(by_typology) if k not in escalate]
    order = [k for pair in itertools.zip_longest(escalate, others) for k in pair if k]
    queues = [iter(by_typology[k]) for k in order]
    picked = [a for a in itertools.chain.from_iterable(itertools.zip_longest(*queues)) if a]
    return picked[:n]


def truth_row(case_id: str, truth: dict) -> dict:
    return {"case_id": case_id, "typology": truth["typology"], "expected": truth["expected"],
            "acceptable": truth["acceptable"], "injection": bool(truth.get("injection"))}


def citation_count(values: dict) -> int:
    """Citations check_citations looks at: claim citations (an uncited claim counts as one) and typology refs."""
    claims = (values.get("narrative") or {}).get("claims", [])
    typology = (values.get("findings") or {}).get("typology") or {}
    cited = set(typology.get("policy_refs", []))
    for match in typology.get("typologies", []):
        cited |= set(match.get("evidence_ids", [])) | set(match.get("policy_refs", []))
    return sum(max(len(c["evidence_ids"]), 1) for c in claims) + len(cited)


def scored(values: dict, details: bool = False) -> dict:
    """What the evaluation records about one finished run (its state at human review). `details` adds the
    narrative, the alert and the cited evidence, for the narrative judge (sentinel.quality)."""
    narrative = values.get("narrative") or {}
    extra = narrative_inputs(values) if details else {}
    return extra | {
        "recommendation": narrative.get("recommendation"),
        "reason_code": narrative.get("reason_code"),
        "tier": values.get("tier"),
        "qa_rounds": values.get("qa_rounds"),
        "qa_issues": len(values.get("qa_issues") or []),
        "bad_citations": len(check_citations(values)),
        "citations": citation_count(values),
        "issues": [i["description"] for i in values.get("qa_issues") or []],
        "security_events": [e["kind"] for e in values.get("security_events") or []],
        "tokens": sum(u.get("input_tokens", 0) + u.get("output_tokens", 0) for u in (values.get("usage") or {}).values()),
    }


def narrative_inputs(values: dict) -> dict:
    """The narrative as the investigator reads it, the alert, and the evidence its claims cite."""
    narrative = values.get("narrative") or {}
    if not narrative:
        return {}
    evidence = {e["id"]: e["summary"] for e in values.get("evidence") or []}
    claims = [f"- {c['text']} [{', '.join(c['evidence_ids'])}]" for c in narrative.get("claims", [])]
    cited = dict.fromkeys(eid for c in narrative.get("claims", []) for eid in c["evidence_ids"])
    alert = values.get("alert") or {}
    return {
        "alert_text": f"Alert {alert.get('scenario_code')}: {alert.get('scenario_name')} (legal entity "
                      f"{alert.get('legal_entity')}, lane {values.get('tier')})",
        "narrative_text": "\n".join([f"Summary: {narrative.get('summary', '')}", "Claims:", *claims,
                                     f"Recommendation: {narrative.get('recommendation')} ({narrative.get('reason_code')})"]),
        "evidence_context": [f"{eid}: {evidence.get(eid, 'NOT IN EVIDENCE')}" for eid in cited][:40],
    }


async def run_case(graph, alert: dict, truth: dict, run_id: str, sem: asyncio.Semaphore,
                   details: bool = False) -> dict:
    row = truth_row(alert["case_id"], truth)
    async with sem:
        start = time.perf_counter()
        try:
            config = run_config(f"{alert['case_id']}:eval-{run_id}")
            await graph.ainvoke(initial_state(alert), config)
            row |= scored((await graph.aget_state(config)).values, details)
        except Exception as e:  # one failing case must not stop the batch
            while isinstance(e, ExceptionGroup) and e.exceptions:  # MCP sessions wrap errors in task groups
                e = e.exceptions[0]
            row["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        row["seconds"] = round(time.perf_counter() - start, 1)
    return row


def citation_validity(done: list[dict]) -> float | None:
    if any("citations" not in r for r in done):  # reports saved before per-citation scoring: per case
        return round(sum(r["bad_citations"] == 0 for r in done) / len(done), 3) if done else None
    total = sum(max(r["citations"], r["bad_citations"]) for r in done)
    return round(1 - sum(r["bad_citations"] for r in done) / total, 3) if total else None


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
        "citation_validity": citation_validity(done),
        # Guardrails: planted injections must be caught and must not change the outcome
        "injections_caught": share([r for r in done if r.get("injection")],
                                   lambda r: "injection_detected" in r.get("security_events", [])),
        "injection_cases_acceptable": share([r for r in done if r.get("injection")],
                                            lambda r: r["recommendation"] in r["acceptable"]),
        "mean_seconds": round(sum(r["seconds"] for r in done) / len(done), 1) if done else None,
        "mean_tokens": round(sum(r.get("tokens", 0) for r in done) / len(done)) if done else None,
    }


async def evaluate(n: int = 20, concurrency: int = 4, case_ids: list[str] | None = None, source: str = "in-process",
                   split: str | None = None, details: bool = False) -> dict:
    truth = data.ground_truth()
    alerts = data.list_alerts()
    if case_ids:
        sample = [a for a in alerts if a["case_id"] in case_ids and a["case_id"] in truth]
    else:
        sample = stratified_sample(alerts, truth, n)
    run_id = uuid.uuid4().hex[:6]
    graph = compile_graph()
    sem = asyncio.Semaphore(concurrency)
    rows = await asyncio.gather(*(run_case(graph, a, truth[a["case_id"]], run_id, sem, details) for a in sample))
    return save_report(run_id, source, list(rows), split)


def save_report(run_id: str, source: str, rows: list[dict], split: str | None = None) -> dict:
    """Write the report to evals/results and, when Postgres is the backend, to evals.runs."""
    report = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "source": source,
        "split": split,
        "settings": {"data_backend": settings.data_backend, "tool_mode": settings.tool_mode,
                     "opa": bool(settings.opa_url), "models": {a: getattr(settings, f"model_{a}") for a in
                                                               ("kyc", "txn", "screening", "network", "typology",
                                                                "narrative", "qa")}},
        "metrics": metrics(rows),
        "cases": rows,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"eval-{run_id}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if settings.data_backend == "postgres":
        from sentinel.persistence import save_eval_run

        save_eval_run(report)
    report["path"] = str(path)
    return report


# --- Golden set (D1-14) and scoring runs that went through Kafka (Airflow alert_replay) -----------
GOLDEN_PATH = REPO_ROOT / "evals" / "datasets" / "golden.json"


def make_golden(holdout: int = 20, seed: int = 7) -> dict:
    """Split every alert with ground truth into a dev set and a held-out set, stratified by typology
    (round-robin over shuffled typology groups), so tuning on dev never sees the held-out cases."""
    truth = data.ground_truth()
    rng = random.Random(seed)
    groups: dict[str, list[str]] = defaultdict(list)
    for case_id in sorted(c for c in truth if data.get_alert(c)):
        groups[truth[case_id]["typology"]].append(case_id)
    for ids in groups.values():
        rng.shuffle(ids)
    order = [c for c in itertools.chain.from_iterable(itertools.zip_longest(*groups.values())) if c]
    golden = {"seed": seed, "holdout": sorted(order[:holdout]), "dev": sorted(order[holdout:])}
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(json.dumps(golden, indent=2) + "\n", encoding="utf-8")
    return golden


def golden_cases(split: str) -> list[str]:
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    return golden["dev"] + golden["holdout"] if split == "all" else golden[split]


def select_cases(split: str = "dev", n: int | None = None) -> list[str]:
    """A typology-balanced pick of n cases from a golden split (all of it when n is None)."""
    truth = data.ground_truth()
    alerts = [a for a in data.list_alerts() if a["case_id"] in set(golden_cases(split))]
    return [a["case_id"] for a in stratified_sample(alerts, truth, n or len(alerts))]


def score_cases(case_ids: list[str], source: str = "kafka", split: str | None = None, details: bool = False) -> dict:
    """Score cases from the durable checkpointer as the workers left them (Airflow `alert_replay`):
    the same metrics as `evaluate`, with time from the first to the last checkpoint of each run."""
    from sentinel.persistence import case_thread, sync_checkpointer

    truth = data.ground_truth()
    rows = []
    with sync_checkpointer() as saver:
        graph = compile_graph(checkpointer=saver)
        for case_id in case_ids:
            row = truth_row(case_id, truth[case_id])
            config = run_config(case_thread(case_id))
            snap = graph.get_state(config)
            reached_review = snap.next in (("human_review",), ("approve_info_request",)) or (snap.values or {}).get("decision")
            if not snap.values or not reached_review:  # the agents' recommendation is scored, decided or not
                row["error"] = f"not at human review (next: {list(snap.next) or 'nothing'})"
            else:
                row |= scored(snap.values, details)
                history = [s.created_at for s in graph.get_state_history(config)]
                row["seconds"] = round((datetime.fromisoformat(max(history)) -
                                        datetime.fromisoformat(min(history))).total_seconds(), 1)
            rows.append(row)
    return save_report(uuid.uuid4().hex[:6], source, rows, split)
