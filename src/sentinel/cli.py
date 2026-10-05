"""Sentinel command line.

    sentinel run data/fixtures/alerts/CASE-0001.json            # asks you for the decision
    sentinel run --case CASE-G0001 --accept                      # alert from the data backend
    sentinel data generate                                       # synthetic dataset -> data/generated/
    sentinel data load                                           # fixtures + dataset -> Postgres
    sentinel eval --n 20                                         # score recommendations vs ground truth
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from langgraph.types import Command

from sentinel import data
from sentinel.config import settings
from sentinel.graph import compile_graph, initial_state, run_config
from sentinel.hitl import validate_decision
from sentinel.schemas import REASON_CODES

RULE = "-" * 72


def specialist_line(agent: str, f: dict) -> str:
    if agent == "kyc":
        return f"kyc: risk {f['risk_rating']}, {len(f['discrepancies'])} discrepancy(ies)"
    if agent == "txn":
        return f"txn: {len(f['red_flags'])} red flag(s) {[r['code'] for r in f['red_flags']]}"
    true_hits = [h["entry_id"] for h in f["hits"] if h["is_true_match"]]
    media = [m["article_id"] for m in f["adverse_media"] if m["is_about_customer"]]
    return (f"screening: {len(f['hits'])} list candidate(s), true matches {true_hits or 'none'}, "
            f"relevant media {media or 'none'}")


def describe_update(node: str, out: dict | None) -> list[str]:
    """One-line summaries of a graph node's state update (shared by the CLI and the test console)."""
    out = out or {}
    findings = out.get("findings", {})
    if node == "triage":
        t = findings["triage"]
        return [f"[triage]    lane={t['tier']}  rule hits={t['rule_hits'] or 'none'}"]
    if agents := [a for a in ("kyc", "txn", "screening") if a in findings]:
        return [
            f"[{node:<9}] {specialist_line(a, findings[a])}, {len(out.get('evidence', []))} evidence items"
            for a in agents
        ]
    if node in ("narrative", "rework") and "narrative" in out:
        n = out["narrative"]
        return [f"[{node:<9}] narrative: {len(n['claims'])} claims, recommends {n['recommendation']} "
                f"({n['reason_code']})"]
    if node == "qa":
        issues = out["qa_issues"]
        status = "PASS" if not issues else f"{len(issues)} issue(s) -> rework {out['rework_target']}"
        return [f"[qa]        round {out['qa_rounds']}: {status}"] + [
            f"            - [{i['severity']}] {i['target_agent']}: {i['description']}" for i in issues
        ]
    if node == "human_review":
        d = out["decision"]
        return [f"[decision]  {d['action']} ({d['reason_code']}) by {d['investigator_id']}"]
    return []


def show_update(node: str, out: dict | None) -> None:
    for line in describe_update(node, out):
        print(line)


def show_review_packet(p: dict) -> None:
    n = p["narrative"]
    evidence = {e["id"]: e["summary"] for e in p["evidence"]}
    print(f"\n{RULE}\nHUMAN REVIEW - {p['case_id']} (lane: {p['tier']})\n{RULE}")
    print(f"Summary: {n.get('summary')}\n\nClaims:")
    for i, c in enumerate(n.get("claims", []), 1):
        print(f" {i}. {c['text']}")
        for eid in c["evidence_ids"]:
            print(f"      [{eid}] {evidence.get(eid, '!! UNKNOWN EVIDENCE ID')}")
    if n.get("open_questions"):
        print("\nOpen questions:")
        for q in n["open_questions"]:
            print(f" - {q}")
    if p["qa_issues"]:
        print("\nUnresolved QA issues:")
        for i in p["qa_issues"]:
            print(f" - [{i['severity']}] {i['description']}")
    print(f"\nRecommendation: {p['recommendation']} ({p['reason_code']})\n{RULE}")


def ask_decision(p: dict) -> dict:
    while True:
        action = input(f"Action {list(REASON_CODES)} [{p['recommendation']}]: ").strip() or p["recommendation"]
        default_code = p["reason_code"] if action == p["recommendation"] else REASON_CODES.get(action, [""])[0]
        code = input(f"Reason code {REASON_CODES.get(action, [])} [{default_code}]: ").strip() or default_code
        investigator = input("Investigator ID [INV-0001]: ").strip() or "INV-0001"
        decision = {
            "action": action,
            "reason_code": code,
            "investigator_id": investigator,
            "agree_with_recommendation": action == p["recommendation"],
            "decided_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            validate_decision(decision)
            return decision
        except ValueError as e:
            print(f"Invalid decision: {e}")


async def run(alert: dict, accept: bool) -> int:
    graph = compile_graph()
    config = run_config(alert["case_id"])
    print(f"Running {alert['case_id']} ({alert['scenario_code']})")

    async for update in graph.astream(initial_state(alert), config, stream_mode="updates"):
        for node, out in update.items():
            if node != "__interrupt__":
                show_update(node, out)

    snapshot = await graph.aget_state(config)
    if not snapshot.interrupts:
        print("Run ended without reaching human review.")
        return 1
    packet = snapshot.interrupts[0].value
    show_review_packet(packet)

    if accept:
        decision = {
            "action": packet["recommendation"],
            "reason_code": packet["reason_code"],
            "investigator_id": "INV-0001",
            "agree_with_recommendation": True,
            "decided_at": datetime.now(timezone.utc).isoformat(),
        }
        print(f"--accept: taking the recommendation ({decision['action']})")
    else:
        decision = ask_decision(packet)

    async for update in graph.astream(Command(resume=decision), config, stream_mode="updates"):
        for node, out in update.items():
            show_update(node, out)

    final = await graph.aget_state(config)
    print(f"\nCase {alert['case_id']} complete. Versions: {json.dumps(final.values.get('versions'))}")
    return 0


def print_eval(report: dict) -> None:
    print(f"\n{'case':<12} {'typology':<22} {'expected':<9} {'got':<13} {'lane':<5} {'cites':<6} secs")
    for r in report["cases"]:
        got = r.get("recommendation") or "ERROR"
        mark = "ok" if got in r["acceptable"] else "MISS"
        cites = "-" if "error" in r else ("ok" if r["bad_citations"] == 0 else str(r["bad_citations"]))
        print(f"{r['case_id']:<12} {r['typology']:<22} {r['expected']:<9} {got:<9}{mark:>4} "
              f"{r.get('tier') or '-':<5} {cites:<6} {r['seconds']}")
        if "error" in r:
            print(f"             {r['error']}")
        for issue in r.get("issues", []):
            print(f"             - {issue}")
    print(f"\n{json.dumps(report['metrics'], indent=2)}\nReport: {report['path']}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="sentinel")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Investigate one alert (a JSON file or a case ID from the data backend)")
    run_p.add_argument("alert", nargs="?", help="Path to an alert JSON file")
    run_p.add_argument("--case", help="Case ID to load from the data backend, e.g. CASE-G0001")
    run_p.add_argument("--accept", action="store_true", help="Accept the recommendation without prompting")

    data_p = sub.add_parser("data", help="Generate or load the synthetic dataset")
    data_sub = data_p.add_subparsers(dest="data_command", required=True)
    gen_p = data_sub.add_parser("generate", help="Write data/generated/dataset.json")
    gen_p.add_argument("--seed", type=int, default=42)
    data_sub.add_parser("load", help="Load the fixtures and generated dataset into Postgres (recreates tables)")

    eval_p = sub.add_parser("eval", help="Run alerts with ground truth up to human review and score them")
    eval_p.add_argument("--n", type=int, default=20, help="Number of cases (stratified across typologies)")
    eval_p.add_argument("--concurrency", type=int, default=4)
    eval_p.add_argument("--cases", nargs="+", help="Run only these case IDs")

    args = parser.parse_args()
    if args.command == "run":
        if bool(args.alert) == bool(args.case):
            parser.error("give either an alert file or --case")
        alert = json.loads(Path(args.alert).read_text(encoding="utf-8")) if args.alert else data.get_alert(args.case)
        if not alert:
            parser.error(f"case {args.case} not found in the {settings.data_backend} backend")
        sys.exit(asyncio.run(run(alert, args.accept)))
    elif args.command == "data" and args.data_command == "generate":
        from sentinel.datagen.generator import generate

        out = settings.dataset_paths[-1]
        out.parent.mkdir(parents=True, exist_ok=True)
        dataset = generate(args.seed)
        out.write_text(json.dumps(dataset), encoding="utf-8")
        print(f"Wrote {out}\n{json.dumps(dataset['meta']['counts'])}")
    elif args.command == "data" and args.data_command == "load":
        from sentinel.datagen.loader import load

        counts = load(settings.dataset_paths, settings.pg_dsn)
        print(f"Loaded into {settings.pg_dsn.rsplit('@', 1)[-1]}\n{json.dumps(counts, indent=2)}")
    elif args.command == "eval":
        from sentinel.evaluate import evaluate

        print_eval(asyncio.run(evaluate(args.n, args.concurrency, args.cases)))


if __name__ == "__main__":
    main()
