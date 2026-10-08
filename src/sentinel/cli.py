"""Sentinel command line.

    sentinel run data/fixtures/alerts/CASE-0001.json            # asks you for the decision
    sentinel run --case CASE-G0001 --accept                      # alert from the data backend
    sentinel data generate                                       # synthetic dataset -> data/generated/
    sentinel data load                                           # fixtures + dataset -> Postgres
    sentinel data kb                                             # policy documents -> pgvector
    sentinel data graph                                          # customer network -> Neo4j (+GDS scores)
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
        return f"kyc: risk {f.get('risk_rating', '?')}, {len(f.get('discrepancies', []))} discrepancy(ies)"
    if agent == "txn":
        flags = f.get("red_flags", [])
        return f"txn: {len(flags)} red flag(s) {[r['code'] for r in flags]}"
    if agent == "network":
        return (f"network: {f.get('assessment', '?')}, mule score {f.get('mule_score')}, "
                f"{len(f.get('related_parties', []))} related part(ies)")
    if agent == "typology":
        codes = [t["code"] for t in f.get("typologies", [])]
        return (f"typology: {codes or 'none'}, risk {f.get('risk_score', '?')}, recommends "
                f"{f.get('recommendation', '?')} ({f.get('reason_code', '?')}), {len(f.get('policy_refs', []))} policy ref(s)")
    hits = f.get("hits", [])
    true_hits = [h["entry_id"] for h in hits if h["is_true_match"]]
    media = [m["article_id"] for m in f.get("adverse_media", []) if m["is_about_customer"]]
    return (f"screening: {len(hits)} list candidate(s), true matches {true_hits or 'none'}, "
            f"relevant media {media or 'none'}")


def describe_update(node: str, out: dict | None) -> list[str]:
    """One-line summaries of a graph node's state update (shared by the CLI and the test console)."""
    out = out or {}
    findings = out.get("findings", {})
    if node == "triage":
        t = findings["triage"]
        return [f"[triage]    lane={t['tier']}  rule hits={t['rule_hits'] or 'none'}"]
    if agents := [a for a in ("kyc", "txn", "screening", "network", "typology") if a in findings]:
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
        critic = findings.get("qa")
        verdict = (f"critic {'passed' if critic['passed'] else 'failed'}" if critic else "code checks only")
        if not issues:
            status = "PASS"
        elif out["rework_target"]:
            status = f"{len(issues)} issue(s) -> rework {out['rework_target']}"
        else:
            status = f"PASS with {len(issues)} minor note(s)"
        return [f"[qa]        round {out['qa_rounds']}: {status} ({verdict})"] + [
            f"            - [{i['severity']}] {i['target_agent']}: {i['description']}" for i in issues
        ]
    if node == "draft_info_request":
        r = out["info_request"]
        rail = "passed" if r["rail"]["passed"] else f"FAILED {r['rail']['violations']}"
        return [f"[request]   customer info request drafted: {len(r['questions'])} question(s), "
                f"tipping-off rail {rail} (attempt {r['rail']['attempts']})"]
    if node == "approve_info_request":
        r = out["info_request"]
        return [f"[approval]  customer info request {r['status']} by {r['approver_id']}"]
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
    if snapshot.interrupts and snapshot.interrupts[0].value.get("kind") == "approval":
        draft = snapshot.interrupts[0].value["draft"]
        print(f"\n{RULE}\nCUSTOMER INFORMATION REQUEST (approval needed)\n{RULE}\n{draft['message']}")
        for q in draft["questions"]:
            print(f" - {q}")
        print(f"Tipping-off rail: {'passed' if draft['rail']['passed'] else draft['rail']['violations']}")
        answer = "approve" if accept else (input("approve / reject [approve]: ").strip() or "approve")
        approval = {"action": answer, "approver_id": "INV-0001"}
        async for update in graph.astream(Command(resume=approval), config, stream_mode="updates"):
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


async def kafka_publish(alerts: list[dict]) -> None:
    from sentinel import kafka
    from sentinel.events import ALERTS_TOPIC, AlertEvent

    prod = kafka.producer()
    await prod.start()
    try:
        for alert in alerts:
            await kafka.send(prod, ALERTS_TOPIC, AlertEvent.model_validate(alert))
            print(f"published alert {alert['case_id']}")
    finally:
        await prod.stop()


async def kafka_decide(case_ids: list[str], action: str | None, reason_code: str | None, investigator: str,
                       accept: bool) -> None:
    """Publish decisions; with --accept, take each case's recommendation from its checkpoint."""
    from sentinel import kafka
    from sentinel.events import DECISIONS_TOPIC, DecisionEvent
    from sentinel.persistence import durable_state

    async with durable_state() as (checkpointer, cases):
        graph = compile_graph(checkpointer=checkpointer)
        if not case_ids:  # all cases waiting for review
            async with cases.pool.connection() as conn:
                rows = await (await conn.execute(
                    "SELECT case_id FROM cases.alerts WHERE status = 'awaiting_review' ORDER BY case_id")).fetchall()
            case_ids = [r["case_id"] for r in rows]
        prod = kafka.producer()
        await prod.start()
        try:
            for case_id in case_ids:
                act, code = action, reason_code
                if accept:
                    snapshot = await graph.aget_state(run_config(await cases.thread_for(case_id)))
                    if not snapshot.interrupts:
                        print(f"skipped {case_id}: not waiting for review")
                        continue
                    packet = snapshot.interrupts[0].value
                    act, code = packet["recommendation"], packet["reason_code"]
                event = DecisionEvent(case_id=case_id, action=act, reason_code=code, investigator_id=investigator,
                                      agree_with_recommendation=True if accept else None)
                await kafka.send(prod, DECISIONS_TOPIC, event)
                print(f"published decision {case_id}: {act} ({code})")
        finally:
            await prod.stop()


async def kafka_approve(case_id: str, action: str, approver: str, message: str | None) -> None:
    from sentinel import kafka
    from sentinel.events import DECISIONS_TOPIC, ApprovalEvent

    await kafka.publish(DECISIONS_TOPIC, [ApprovalEvent(case_id=case_id, action=action, approver_id=approver,
                                                        message=message)])
    print(f"published approval {case_id}: {action}")


async def kafka_reply(case_id: str, text: str) -> None:
    from sentinel import kafka
    from sentinel.events import FOLLOWUPS_TOPIC, FollowUpEvent

    await kafka.publish(FOLLOWUPS_TOPIC, [FollowUpEvent(case_id=case_id, reply_text=text)])
    print(f"published customer reply for {case_id}")


async def run_redteam(case_id: str) -> int:
    from sentinel.redteam import probe

    alert = data.get_alert(case_id) or {"legal_entity": "UK"}
    rows = await probe(case_id, alert["legal_entity"])
    print(f"\n{'agent':<10} {'tool':<17} {'result':<8} attempt / reason")
    for r in rows:
        print(f"{r['agent']:<10} {r['tool']:<17} {'DENIED' if r['denied'] else 'ALLOWED':<8} {r['attempt']}")
        if r["reason"]:
            print(f"{'':<37}{r['reason']}")
    denied = sum(r["denied"] for r in rows)
    print(f"\n{denied}/{len(rows)} forged tool calls denied by OPA and logged as security events")
    return 0 if denied == len(rows) else 1


async def kafka_tail(case_ids: set[str], from_beginning: bool) -> None:
    from sentinel import kafka
    from sentinel.events import CASE_EVENTS_TOPIC

    consumer = kafka.consumer(CASE_EVENTS_TOPIC, group_id=None,
                              auto_offset_reset="earliest" if from_beginning else "latest")
    await consumer.start()
    try:
        async for msg in consumer:
            event = kafka.decode(msg.value)
            if case_ids and event["case_id"] not in case_ids:
                continue
            extra = event.get("detail") or (json.dumps(event["data"]) if event.get("data") else "")
            print(f"{event['at'][11:19]} {event['case_id']:<11} {event['type']:<17} {event.get('node') or '':<12} {extra}")
    finally:
        await consumer.stop()


async def run_workers(kind: str, concurrency: int) -> None:
    from sentinel.persistence import durable_state
    from sentinel.workers import HANDLERS, run_worker

    async with durable_state() as (checkpointer, cases):
        graph = compile_graph(checkpointer=checkpointer)
        print(f"worker '{kind}' consuming {list(HANDLERS[kind])} (concurrency {concurrency}); Ctrl+C to stop")
        await run_worker(graph, cases, HANDLERS[kind], concurrency)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.platform == "win32":  # psycopg's async driver cannot use the default Proactor loop on Windows
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
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
    data_sub.add_parser("load", help="Load the fixtures and generated dataset into Postgres "
                                     "(recreates the data tables and clears case runs)")
    data_sub.add_parser("kb", help="Embed the policy documents into the pgvector knowledge base")
    data_sub.add_parser("graph", help="Load the customer network into Neo4j and compute scores (GDS)")

    eval_p = sub.add_parser("eval", help="Run alerts with ground truth up to human review and score them")
    eval_p.add_argument("--n", type=int, default=20, help="Number of cases (stratified across typologies)")
    eval_p.add_argument("--concurrency", type=int, default=4)
    eval_p.add_argument("--cases", nargs="+", help="Run only these case IDs")

    kafka_p = sub.add_parser("kafka", help="Kafka topics, alerts, decisions and case events")
    kafka_sub = kafka_p.add_subparsers(dest="kafka_command", required=True)
    kafka_sub.add_parser("init", help="Create the Sentinel topics")
    pub_p = kafka_sub.add_parser("publish", help="Publish alerts from the data backend")
    pub_p.add_argument("--cases", nargs="+", help="Case IDs to publish")
    pub_p.add_argument("--sample", type=int, help="Publish N alerts stratified across typologies")
    dec_p = kafka_sub.add_parser("decide", help="Publish human decisions")
    dec_p.add_argument("cases", nargs="*", help="Case IDs (default: every case awaiting review)")
    dec_p.add_argument("--accept", action="store_true", help="Take each case's recommendation")
    dec_p.add_argument("--action", choices=list(REASON_CODES))
    dec_p.add_argument("--reason-code")
    dec_p.add_argument("--investigator", default="INV-0001")
    tail_p = kafka_sub.add_parser("tail", help="Print case events as they arrive")
    tail_p.add_argument("cases", nargs="*", help="Only these case IDs")
    tail_p.add_argument("--from-beginning", action="store_true")
    appr_p = kafka_sub.add_parser("approve", help="Approve, edit or reject a customer information request")
    appr_p.add_argument("case")
    appr_p.add_argument("--action", choices=["approve", "edit", "reject"], default="approve")
    appr_p.add_argument("--message", help="Edited message (with --action edit)")
    appr_p.add_argument("--approver", default="INV-0001")
    reply_p = kafka_sub.add_parser("reply", help="Simulate the customer's reply to a request for information")
    reply_p.add_argument("case")
    reply_p.add_argument("--text", required=True)

    redteam_p = sub.add_parser("redteam", help="Red-team probe: forged forbidden tool calls must be denied by OPA and logged")
    redteam_p.add_argument("--case", default="CASE-0001")

    worker_p = sub.add_parser("worker", help="Run Kafka workers (agents on Bedrock, state in Postgres)")
    worker_p.add_argument("kind", choices=["alerts", "decisions", "followups", "all"], nargs="?", default="all")
    worker_p.add_argument("--concurrency", type=int, default=4, help="Cases processed in parallel")

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
    elif args.command == "data" and args.data_command == "kb":
        from sentinel.kb import load_policies

        print(f"Embedded {load_policies()} policy sections into kb.policy_chunks ({settings.model_embedding})")
    elif args.command == "data" and args.data_command == "graph":
        from sentinel.graphdb import load_graph

        if not settings.neo4j_uri:
            parser.error("set SENTINEL_NEO4J_URI (and user/password) in .env")
        print(f"Loaded the customer network into Neo4j: {json.dumps(load_graph())}")
    elif args.command == "eval":
        from sentinel.evaluate import evaluate

        print_eval(asyncio.run(evaluate(args.n, args.concurrency, args.cases)))
    elif args.command == "kafka" and args.kafka_command == "init":
        from sentinel.kafka import create_topics

        created = asyncio.run(create_topics())
        print(f"created: {created or 'nothing (all topics exist)'}")
    elif args.command == "kafka" and args.kafka_command == "publish":
        from sentinel.evaluate import stratified_sample

        if args.cases:
            alerts = [a for cid in args.cases if (a := data.get_alert(cid))]
        elif args.sample:
            alerts = stratified_sample(data.list_alerts(), data.ground_truth(), args.sample)
        else:
            parser.error("give --cases or --sample")
        asyncio.run(kafka_publish(alerts))
    elif args.command == "kafka" and args.kafka_command == "decide":
        if not args.accept and not (args.action and args.reason_code):
            parser.error("give --accept, or --action and --reason-code")
        asyncio.run(kafka_decide(args.cases, args.action, args.reason_code, args.investigator, args.accept))
    elif args.command == "kafka" and args.kafka_command == "approve":
        if args.action == "edit" and not args.message:
            parser.error("--action edit needs --message")
        asyncio.run(kafka_approve(args.case, args.action, args.approver, args.message))
    elif args.command == "kafka" and args.kafka_command == "reply":
        asyncio.run(kafka_reply(args.case, args.text))
    elif args.command == "redteam":
        sys.exit(asyncio.run(run_redteam(args.case)))
    elif args.command == "kafka" and args.kafka_command == "tail":
        try:
            asyncio.run(kafka_tail(set(args.cases), args.from_beginning))
        except KeyboardInterrupt:
            pass
    elif args.command == "worker":
        import logging

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s",
                            datefmt="%H:%M:%S")
        for noisy in ("httpx", "aiokafka", "mcp", "botocore"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        try:
            asyncio.run(run_workers(args.kind, args.concurrency))
        except KeyboardInterrupt:
            print("worker stopped")


if __name__ == "__main__":
    main()
