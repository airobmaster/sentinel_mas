"""Graph: triage -> [kyc | txn | screening] -> narrative -> qa -> (rework | human_review) -> END.

Same shape as TDD §5.2; the network and typology nodes come in a later slice, so the join
currently feeds narrative directly.
"""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from sentinel.agents.kyc import kyc
from sentinel.agents.narrative import narrative
from sentinel.agents.qa import qa
from sentinel.agents.screening import screening
from sentinel.agents.triage import triage
from sentinel.agents.txn import txn
from sentinel.config import settings
from sentinel.guards.citations import FAST_LANE_SPECIALISTS
from sentinel.hitl import human_review
from sentinel.state import CaseState

NEXT_AFTER_REWORK = {"kyc": "narrative", "txn": "narrative", "screening": "narrative", "narrative": "qa"}
MAX_QA_ROUNDS = 2  # one rework, then a human sees the remaining issues


def route_after_qa(state: CaseState) -> str:
    if state.get("qa_issues") and state["qa_rounds"] < MAX_QA_ROUNDS:
        return "rework"
    return "human_review"


def build_graph(nodes: dict | None = None) -> StateGraph:
    """`nodes` overrides node functions by name (used by tests to stub the agents)."""
    fns = {
        "triage": triage,
        "kyc": kyc,
        "txn": txn,
        "screening": screening,
        "narrative": narrative,
        "qa": qa,
        **(nodes or {}),
    }
    specialists = {name: fns[name] for name in NEXT_AFTER_REWORK}

    async def rework(state: CaseState) -> dict:
        return await specialists[state["rework_target"]](state)

    retry = RetryPolicy(max_attempts=3)
    g = StateGraph(CaseState)
    g.add_node("triage", fns["triage"])
    for name in (*FAST_LANE_SPECIALISTS, "narrative"):
        g.add_node(name, fns[name], retry_policy=retry)
    g.add_node("qa", fns["qa"])
    g.add_node("rework", rework, retry_policy=retry)
    g.add_node("human_review", human_review)  # no retry: interrupt node

    g.add_edge(START, "triage")
    for name in FAST_LANE_SPECIALISTS:
        g.add_edge("triage", name)  # parallel fan-out
    g.add_edge(list(FAST_LANE_SPECIALISTS), "narrative")  # waits for all three
    g.add_edge("narrative", "qa")
    g.add_conditional_edges("qa", route_after_qa, ["rework", "human_review"])
    # A reworked specialist rejoins at narrative directly, bypassing the join (TDD §5.2).
    g.add_conditional_edges("rework", lambda s: NEXT_AFTER_REWORK[s["rework_target"]], ["narrative", "qa"])
    g.add_edge("human_review", END)
    return g


def compile_graph(checkpointer=None, nodes: dict | None = None):
    return build_graph(nodes).compile(checkpointer=checkpointer or InMemorySaver())


def initial_state(alert: dict) -> CaseState:
    return {
        "case_id": alert["case_id"],
        "legal_entity": alert["legal_entity"],
        "alert": alert,
        "evidence": [],
        "findings": {},
        "versions": {},
    }


def run_config(thread_id: str) -> dict:
    """thread_id is the case_id in production; test tools may add a suffix to rerun a case."""
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": settings.graph_recursion_limit}
