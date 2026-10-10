"""Graph (TDD §5.2):

    triage -> [kyc | txn | screening] -> gate -> (full lane) network -> typology -> narrative -> qa
                                              -> (fast lane) ----------> typology
    qa -> rework (once, for blocker/major issues)
       -> draft_info_request -> approve_info_request (UC-03, when the recommendation is request_info)
       -> human_review (L1 for the fast lane, L2 for the full lane: BR-08)
            -- escalate --> l2_review (from L1) -- escalate --> mlro_review (file a SAR or not) -> END
            -- close / request info --> END
"""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from sentinel.agents.info_request import draft_info_request
from sentinel.agents.kyc import kyc
from sentinel.agents.narrative import narrative
from sentinel.agents.network import network
from sentinel.agents.qa import REWORK_SEVERITIES, qa
from sentinel.agents.screening import screening
from sentinel.agents.triage import triage
from sentinel.agents.txn import txn
from sentinel.agents.typology import typology
from sentinel.config import settings
from sentinel.guards.citations import FAST_LANE_SPECIALISTS
from sentinel.hitl import approve_info_request, human_review, l2_review, mlro_review, recommendation_of, route_after_review
from sentinel.state import CaseState

# A reworked specialist rejoins downstream of the join: a waiting join would never fire again.
NEXT_AFTER_REWORK = {"kyc": "typology", "txn": "typology", "screening": "typology", "network": "typology",
                     "typology": "narrative", "narrative": "qa"}
MAX_QA_ROUNDS = 2  # one rework, then a human sees the remaining issues


def route_after_gate(state: CaseState) -> str:
    return "network" if state.get("tier") == "full" else "typology"


def route_after_qa(state: CaseState) -> str:
    serious = any(i["severity"] in REWORK_SEVERITIES for i in state.get("qa_issues") or [])
    if serious and state["qa_rounds"] < MAX_QA_ROUNDS and not state.get("budget_exceeded"):
        return "rework"
    if recommendation_of(state).get("recommendation") == "request_info" and not state.get("budget_exceeded"):
        return "draft_info_request"  # UC-03: the customer request needs approval first
    return "human_review"


def build_graph(nodes: dict | None = None) -> StateGraph:
    """`nodes` overrides node functions by name (used by tests to stub the agents)."""
    fns = {
        "triage": triage, "kyc": kyc, "txn": txn, "screening": screening, "network": network,
        "typology": typology, "narrative": narrative, "qa": qa, "draft_info_request": draft_info_request,
        **(nodes or {}),
    }
    specialists = {name: fns[name] for name in NEXT_AFTER_REWORK}

    async def rework(state: CaseState) -> dict:
        return await specialists[state["rework_target"]](state)

    retry = RetryPolicy(max_attempts=3)
    g = StateGraph(CaseState)
    g.add_node("triage", fns["triage"])
    for name in (*FAST_LANE_SPECIALISTS, "network", "typology", "narrative"):
        g.add_node(name, fns[name], retry_policy=retry)
    g.add_node("gate", lambda state: {})
    g.add_node("qa", fns["qa"], retry_policy=retry)
    g.add_node("rework", rework, retry_policy=retry)
    g.add_node("draft_info_request", fns["draft_info_request"], retry_policy=retry)
    g.add_node("approve_info_request", approve_info_request)  # no retry: interrupt node (UC-03)
    g.add_node("human_review", human_review)  # no retry: interrupt nodes (first review level, by lane)
    g.add_node("l2_review", l2_review)  # after an L1 escalation
    g.add_node("mlro_review", mlro_review)  # after an L2 escalation

    g.add_edge(START, "triage")
    for name in FAST_LANE_SPECIALISTS:
        g.add_edge("triage", name)  # parallel fan-out
    g.add_edge(list(FAST_LANE_SPECIALISTS), "gate")  # waits for all three
    g.add_conditional_edges("gate", route_after_gate, ["network", "typology"])
    g.add_edge("network", "typology")
    g.add_edge("typology", "narrative")
    g.add_edge("narrative", "qa")
    g.add_conditional_edges("qa", route_after_qa, ["rework", "draft_info_request", "human_review"])
    g.add_conditional_edges("rework", lambda s: NEXT_AFTER_REWORK[s["rework_target"]],
                            ["typology", "narrative", "qa"])
    g.add_edge("draft_info_request", "approve_info_request")
    g.add_edge("approve_info_request", "human_review")
    g.add_conditional_edges("human_review", route_after_review, ["l2_review", "mlro_review", END])
    g.add_conditional_edges("l2_review", route_after_review, ["mlro_review", END])
    g.add_edge("mlro_review", END)
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
        "security_events": [],
        "usage": {},
        "pii_vault": {},
        "decisions": [],
    }


def follow_up_state(prior: dict, prior_thread: str, reply_text: str, received_at: str) -> CaseState:
    """UC-04: a new run after the customer replied to a request for information. It starts from the
    original alert, with the reply and a summary of the previous review as citable evidence."""
    alert = prior["alert"]
    round_no = (prior.get("follow_up") or {}).get("round", 0) + 1
    narrative, decision = prior.get("narrative") or {}, prior.get("decision") or {}
    source = recommendation_of(prior)
    reply_id, prior_ref = f"reply:{alert['case_id']}-r{round_no}", f"case:{prior_thread}"
    prior_summary = (f"recommendation {source.get('recommendation')} ({source.get('reason_code')}); investigator "
                     f"decided {decision.get('action')} ({decision.get('reason_code')}). {narrative.get('summary', '')}")
    state = initial_state(alert)
    state["evidence"] = [
        {"id": reply_id, "source": "case_mgmt.attach_customer_reply", "agent": "follow_up",
         "summary": f"Customer reply received {received_at[:10]}: {reply_text}"},
        {"id": prior_ref, "source": "case_mgmt.get_case_history", "agent": "follow_up",
         "summary": f"Previous review of this alert: {prior_summary}"},
    ]
    state["pii_vault"] = dict(prior.get("pii_vault") or {})
    if decision.get("level") in ("l1", "l2"):
        state["review_level"] = decision["level"]  # the level that asked the customer reviews the reply
    state["follow_up"] = {"round": round_no, "prior_thread": prior_thread, "reply_id": reply_id,
                          "reply_text": reply_text, "received_at": received_at, "prior_ref": prior_ref,
                          "prior_summary": prior_summary,
                          "questions": (prior.get("info_request") or {}).get("questions")}
    return state


def follow_up_thread(case_id: str, state: CaseState) -> str:
    return f"{case_id}:r{state['follow_up']['round']}"


def run_config(thread_id: str) -> dict:
    """thread_id is the case_id in production; test tools may add a suffix to rerun a case. With tracing on,
    the run reports its steps, model calls and tool calls as spans tagged with the case ID."""
    from sentinel.telemetry import callbacks_for

    case_id = thread_id.split(":", 1)[0]
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": settings.graph_recursion_limit,
            "callbacks": callbacks_for(case_id), "metadata": {"case_id": case_id, "thread_id": thread_id}}
