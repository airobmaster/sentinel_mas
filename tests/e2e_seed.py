"""Test cases for the workbench's Playwright tests (ui/workbench/e2e): copies of fixture alerts run with
stubbed agents (no Bedrock) into the Postgres checkpointer, paused where each browser test starts:

  CASE-E2E-DECIDE-xxxx   awaiting review (fast lane, recommends escalate)    -> decide in the browser
  CASE-E2E-APPROVE-xxxx  awaiting approval of a customer information request -> approve, then request info,
                                                                          then attach the customer's reply
  CASE-E2E-QA-xxxx       closed by L1, its automated QA flagged             -> label it as a QA reviewer
The real worker (`sentinel worker all`) applies the browser's decisions: resuming at a human step needs
no model calls. Only the follow-up run after the reply runs real agents.

    python -m tests.e2e_seed seed     (Playwright global setup)
    python -m tests.e2e_seed clean
"""

import asyncio
import json
import sys
import uuid

import psycopg
from psycopg.types.json import Jsonb

from langgraph.types import Command

from sentinel import data
from sentinel.config import settings
from sentinel.graph import compile_graph, initial_state, run_config
from sentinel.persistence import durable_state, reset_case
from tests import stubs

# A fresh suffix per run: a follow-up started by an earlier run (real agents, minutes long) can never
# write into this run's cases
FLOWS = {"decide": ("CASE-0001", stubs.nodes), "approve": ("CASE-0002", stubs.request_info_nodes),
         "qa": ("CASE-0001", stubs.nodes)}  # decided while seeding and QA-flagged: the QA reviewer's queue (BR-18)


def clean() -> None:
    """Remove the cases of earlier runs."""
    with psycopg.connect(settings.pg_dsn, autocommit=True) as conn:
        old = [r[0] for r in conn.execute("SELECT case_id FROM cases.alerts WHERE case_id LIKE 'CASE-E2E-%'")]
        for case_id in old:
            reset_case(case_id)
            conn.execute("DELETE FROM cases.customer_replies WHERE case_id = %s", (case_id,))
            conn.execute("DELETE FROM cases.alerts WHERE case_id = %s", (case_id,))


async def seed() -> dict:
    clean()
    settings.qa_llm_critic = False  # stubbed agents only: no model calls while seeding
    suffix = uuid.uuid4().hex[:4].upper()
    out = {}
    async with durable_state() as (checkpointer, cases):
        for flow, (source, nodes) in FLOWS.items():
            case_id = f"CASE-E2E-{flow.upper()}-{suffix}"
            alert = {**data.get_alert(source), "case_id": case_id}
            async with cases.pool.connection() as conn:
                await conn.execute("INSERT INTO cases.alerts (case_id, legal_entity, customer_id, alert) "
                                   "VALUES (%s, %s, %s, %s)", (case_id, alert["legal_entity"], alert["customer_id"],
                                                               Jsonb(alert)))
            await cases.start(alert)
            graph = compile_graph(checkpointer=checkpointer, nodes=nodes())
            await graph.ainvoke(initial_state(alert), run_config(case_id))
            if flow == "qa":
                await graph.ainvoke(Command(resume={"action": "close", "reason_code": "FP_EXPLAINED_ACTIVITY",
                                                    "investigator_id": "l1.investigator"}), run_config(case_id))
            snap = await graph.aget_state(run_config(case_id))
            status = ("closed" if flow == "qa" else
                      "awaiting_approval" if snap.next == ("approve_info_request",) else "awaiting_review")
            await cases.set_status(case_id, status, tier=snap.values.get("tier"), qa_flagged=flow == "qa",
                                   assigned_role="l2" if status == "awaiting_approval" else "l1")
            out[flow] = case_id
    return out


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if sys.argv[1:] == ["clean"]:
        clean()
        print("{}")
    else:
        print(json.dumps(asyncio.run(seed())))
