"""Run one alert end to end from the terminal.

    sentinel run data/fixtures/alerts/CASE-0001.json            # asks you for the decision
    sentinel run data/fixtures/alerts/CASE-0001.json --accept   # accepts the recommendation
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from langgraph.types import Command

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


async def run(alert_path: str, accept: bool) -> int:
    alert = json.loads(Path(alert_path).read_text(encoding="utf-8"))
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


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="sentinel")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="Investigate one alert JSON file")
    run_p.add_argument("alert", help="Path to an alert JSON file")
    run_p.add_argument("--accept", action="store_true", help="Accept the recommendation without prompting")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.alert, args.accept)))


if __name__ == "__main__":
    main()
