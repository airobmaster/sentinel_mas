"""Sentinel test console: a developer tool for running alerts end to end and making the human decision.

Both modes have the same three views: Case, Work queue and Event stream.
- Direct: runs the graph in this process with an in-memory checkpointer. No Docker needed; the work
  queue and event stream cover the cases run in this browser session.
- Kafka: publishes the alert to Kafka; a `sentinel worker` process investigates it; the review
  state is read from the Postgres checkpointer; your decision is published to Kafka.
Not the investigator workbench (the React app) and not deployed. Start it with start_sentinel.bat,
or from the repo root:

    .\\.svenv\\Scripts\\streamlit.exe run devtools/streamlit_app.py
"""

import asyncio
import html
import json
import queue
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from langgraph.types import Command

from sentinel import data, kafka
from sentinel.cli import describe_update
from sentinel.config import REPO_ROOT, settings
from sentinel.events import (
    ALERTS_TOPIC,
    DECISIONS_TOPIC,
    FOLLOWUPS_TOPIC,
    AlertEvent,
    ApprovalEvent,
    DecisionEvent,
    FollowUpEvent,
)
from sentinel.graph import (
    build_graph,
    compile_graph,
    follow_up_state,
    initial_state,
    run_config,
)
from sentinel.guardrails.pii import PiiVault
from sentinel.hitl import validate_approval, validate_decision
from sentinel.persistence import (
    OPEN_STATUSES,
    STATUS_AFTER_DECISION,
    case_status,
    case_thread,
    list_cases,
    reset_case,
    sync_checkpointer,
)
from sentinel.schemas import LEVEL_ACTIONS

if sys.platform == "win32":  # psycopg's async driver cannot use the default Proactor loop on Windows
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

ALERT_DIR = REPO_ROOT / "data" / "fixtures" / "alerts"
LEVEL_NAME = {"l1": "L1 analyst", "l2": "L2 investigator", "mlro": "MLRO"}
STATUS_ICON = {"new": "⚪", "in_progress": "🔵", "awaiting_approval": "🟠", "awaiting_review": "🟡", "closed": "🟢",
               "escalated": "🔴", "sar_filed": "🔴", "info_requested": "🟣", "error": "⛔", None: "⚪"}
TERMINAL = ("closed", "escalated", "sar_filed", "info_requested")
ALL_STATUSES = list(OPEN_STATUSES) + list(TERMINAL) + ["error"]
DIRECT, KAFKA, API = "Direct (in-process)", "Kafka (full stack)", "API (full stack)"
MODE_BADGE = {DIRECT: "Direct", KAFKA: "Kafka", API: "API"}

st.set_page_config(page_title="Sentinel · AML Investigation Console", page_icon="🛡️", layout="wide")
state = st.session_state

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, .stApp, .stMarkdown, p, li, label, input, textarea, button, h1, h2, h3, h4, h5, h6,
[data-testid="stMetricValue"], [data-testid="stMetricLabel"], [data-baseweb="tab"] {
    font-family: 'Inter', 'Segoe UI', system-ui, sans-serif !important;
}
.block-container { padding-top: 1.4rem; max-width: 1440px; }
.sn-header { display: flex; justify-content: space-between; align-items: center; gap: 16px;
    padding: 18px 26px; border-radius: 14px; margin-bottom: 14px; color: #fff;
    background: linear-gradient(120deg, #0B2545 0%, #0F4C81 58%, #13798C 100%);
    box-shadow: 0 6px 20px rgba(11, 37, 69, .18); }
.sn-brand { display: flex; gap: 14px; align-items: center; }
.sn-logo { font-size: 36px; line-height: 1; }
.sn-title { font-size: 26px; font-weight: 700; letter-spacing: .2px; line-height: 1.15; }
.sn-sub { font-size: 13.5px; opacity: .85; margin-top: 3px; }
.sn-badges { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
.sn-badge { background: rgba(255,255,255,.14); border: 1px solid rgba(255,255,255,.3); border-radius: 999px;
    padding: 4px 11px; font-size: 12px; font-weight: 500; white-space: nowrap; }
.sn-badge b { font-weight: 700; }
.sn-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: 1px; }
.sn-dot.up { background: #4ADE80; box-shadow: 0 0 0 2px rgba(74, 222, 128, .25); }
.sn-dot.down { background: #FBBF24; box-shadow: 0 0 0 2px rgba(251, 191, 36, .25); }
.sn-legend { color: #5A6B7F; font-size: 12.5px; margin: 2px 0 10px; }
.sn-legend i { display: inline-block; width: 11px; height: 11px; border-radius: 3px; margin: 0 4px -1px 12px;
    border: 1px solid #94A3B8; }
.sn-case-title { font-size: 22px; font-weight: 700; color: #0B2545; margin: 6px 0 2px; }
.sn-case-sub { color: #5A6B7F; font-size: 14px; margin-bottom: 12px; }
.sn-pill { display: inline-block; padding: 3px 11px; border-radius: 999px; font-size: 12.5px; font-weight: 600;
    vertical-align: middle; margin-left: 10px; }
.sn-pill.new { background: #EEF1F5; color: #4A5568; }
.sn-pill.in_progress { background: #E3F0FF; color: #0B5CAD; }
.sn-pill.awaiting_review { background: #FFF4D6; color: #8A5A00; }
.sn-pill.awaiting_approval { background: #FFE8D6; color: #9A4A00; }
.sn-pill.closed { background: #E3F6EA; color: #1E7B45; }
.sn-pill.escalated { background: #FDE7E7; color: #B42318; }
.sn-pill.info_requested { background: #F1E8FF; color: #6B3FA0; }
.sn-pill.error { background: #2D3748; color: #fff; }
[data-testid="stMetric"] { background: #F7F9FC; border: 1px solid #E3E8EF; border-radius: 10px; padding: 9px 14px; }
[data-testid="stMetricValue"] { font-size: 15px; font-weight: 600; color: #0B2545; }
[data-testid="stMetricLabel"] p { font-size: 12px; color: #5A6B7F; }
.sn-issues { width: 100%; border-collapse: collapse; font-size: 13.5px; margin: 4px 0 12px; }
.sn-issues th { text-align: left; background: #F1F4F8; color: #3D4F66; font-weight: 600; padding: 7px 10px;
    border-bottom: 1px solid #DCE3EB; }
.sn-issues td { padding: 7px 10px; border-bottom: 1px solid #EDF1F5; vertical-align: top; color: #1F2D3D; }
.sn-issues .num { text-align: right; font-variant-numeric: tabular-nums; }
.sn-issues tr.total td { font-weight: 700; background: #EEF2F7; border-top: 2px solid #CBD5E1; color: #0B2545; }
.sn-sev { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 12px; font-weight: 600;
    white-space: nowrap; }
.sn-sev.blocker { background: #FDE7E7; color: #B42318; }
.sn-sev.major { background: #FFF1DB; color: #9A4A00; }
.sn-sev.minor { background: #EEF1F5; color: #4A5568; }
/* Never truncate metric labels or values with an ellipsis: wrap whole words instead */
[data-testid="stMetricValue"], [data-testid="stMetricValue"] *, [data-testid="stMetricLabel"],
[data-testid="stMetricLabel"] * { white-space: normal !important; overflow: visible !important;
    text-overflow: clip !important; overflow-wrap: break-word; line-height: 1.3; }
.stTabs [data-baseweb="tab"] { font-weight: 600; font-size: 15px; }
[data-testid="stSidebar"] { border-right: 1px solid #E3E8EF; }
[data-testid="stAppDeployButton"] { display: none; }
.sn-running { display: flex; align-items: center; gap: 12px; padding: 10px 14px; margin: 4px 0 8px;
    background: #EEF5FF; border: 1px solid #CFE0F7; border-radius: 10px; color: #0B2545; font-size: 14px; }
.sn-spin { width: 18px; height: 18px; border-radius: 50%; border: 3px solid #CFE0F7; border-top-color: #0F4C81;
    animation: sn-rotate .8s linear infinite; flex: none; }
@keyframes sn-rotate { to { transform: rotate(360deg); } }
.sn-clock { font-variant-numeric: tabular-nums; font-weight: 700; }
.sn-footer { color: #8592A3; font-size: 12px; text-align: center; margin-top: 32px; padding-top: 12px;
    border-top: 1px solid #E9EDF2; }
</style>
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def running_banner(seconds: float, detail: str) -> str:
    """Spinner + elapsed time; the spinner is CSS, so it keeps turning between page updates."""
    return (f'<div class="sn-running"><div class="sn-spin"></div><span class="sn-clock">{duration(seconds)}</span>'
            f'<span>{detail}</span></div>')


START_EVENTS = ("case_started", "follow_up_started")
PAUSE_EVENTS = ("awaiting_approval", "awaiting_review")
AGENT_LABELS = {"kyc": "KYC", "txn": "Transactions", "screening": "Screening", "network": "Network",
                "typology": "Typology", "narrative": "Narrative", "qa": "QA critic",
                "customer_request": "Customer request"}


def seconds_between(start: str, end: str) -> float:
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()


def latest_start(events: list[dict]) -> dict | None:
    return next((e for e in reversed(events) if e["type"] in START_EVENTS), None)


def current_run(events: list[dict]) -> list[dict]:
    """Events of the latest run (or follow-up run) only: earlier runs of a reset case are left out."""
    start = latest_start(events)
    return [e for e in events if e["at"] >= start["at"]] if start else events


def investigation_seconds(events: list[dict]) -> float | None:
    """Wall-clock time of the latest run (or follow-up run): from its start to the first pause after it."""
    if not (start := latest_start(events)):
        return None
    pause = next((e for e in events if e["type"] in PAUSE_EVENTS and e["at"] >= start["at"]), None)
    return seconds_between(start["at"], pause["at"]) if pause else None


def pill(status: str | None) -> str:
    label = (status or "not started").replace("_", " ")
    return f'<span class="sn-pill {status or "new"}">{label}</span>'


@st.cache_data(ttl=30, show_spinner=False)
def knowledge_sources() -> dict[str, tuple[str, bool]]:
    """Which graph, policy-search and PII backends answer, and whether they are up (checked every 30 s)."""
    from sentinel import graphdb, kb
    from sentinel.guardrails.pii import presidio_status

    return {"Graph": graphdb.describe_backend(), "Policy KB": kb.describe_backend(), "PII": presidio_status()}


def header(mode: str) -> None:
    opa = "on" if settings.opa_url else "off"
    badges = [("Mode", MODE_BADGE[mode]), ("Data", settings.data_backend),
              ("Tools", settings.tool_mode.upper()), ("Policy (OPA)", opa), ("Model", settings.model_narrative)]
    badge_html = "".join(f'<span class="sn-badge">{k}: <b>{v}</b></span>' for k, v in badges)
    badge_html += "".join(
        f'<span class="sn-badge"><span class="sn-dot {"up" if live else "down"}"></span>{k}: <b>{label}</b></span>'
        for k, (label, live) in knowledge_sources().items())
    # Keep the HTML on unindented lines: Markdown renders 4-space-indented lines as a code block.
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="sn-header">'
        '<div class="sn-brand"><div class="sn-logo">🛡️</div>'
        '<div><div class="sn-title">Sentinel</div>'
        '<div class="sn-sub">AML investigation copilot · multi-agent proof of concept on LangGraph</div></div>'
        f'</div><div class="sn-badges">{badge_html}</div></div>',
        unsafe_allow_html=True,
    )


def case_heading(case_id: str, scenario: str, status: str | None) -> None:
    st.markdown(f'<div class="sn-case-title">{case_id}{pill(status)}</div>'
                f'<div class="sn-case-sub">{scenario}</div>', unsafe_allow_html=True)


# --- Shared views -------------------------------------------------------------------------------
def events_frame(events: list[dict]) -> pd.DataFrame:
    """Case events as a table, newest first."""
    rows = [{"time": e["at"][11:19], "case": e["case_id"], "event": e["type"], "node": e.get("node") or "",
             "detail": e.get("detail") or (json.dumps(e["data"]) if e.get("data") else "")}
            for e in sorted(events, key=lambda e: e["at"], reverse=True)]
    return pd.DataFrame(rows, columns=["time", "case", "event", "node", "detail"])


def decision_form(case_id: str, packet: dict) -> dict | None:
    """Render the decision form for the level the case is with (L1, L2 or MLRO); return a validated decision."""
    level = packet.get("level") or "l2"
    level_codes = LEVEL_ACTIONS[level]
    actions = list(level_codes)
    st.markdown(f"#### Your decision · {LEVEL_NAME[level]}")
    if previous := packet.get("decisions"):
        st.caption("Escalated to you: " + " → ".join(f"{LEVEL_NAME.get(d['level'], d['level'])} {d['action']} "
                                                       f"({d['reason_code']})" for d in previous))
    rec = packet.get("recommendation")
    action = st.radio("Action", actions, index=actions.index(rec) if rec in actions else 0, horizontal=True,
                      key=f"action-{case_id}-{level}")
    codes = level_codes[action]
    default = codes.index(packet["reason_code"]) if packet.get("reason_code") in codes else 0
    reason_code = st.selectbox("Reason code", codes, index=default, key=f"code-{case_id}")
    investigator = st.text_input("Investigator ID", "INV-0001", key=f"inv-{case_id}")
    notes = st.text_area("Notes / narrative edits (optional)", key=f"notes-{case_id}")
    if not st.button("Submit decision", type="primary", key=f"submit-{case_id}"):
        return None
    decision = {"action": action, "reason_code": reason_code, "investigator_id": investigator,
                "narrative_edits": notes or None, "agree_with_recommendation": action == rec,
                "decided_at": now()}
    try:
        validate_decision(decision, level)
    except ValueError as e:
        st.error(str(e))
        return None
    return decision


def approval_form(case_id: str, draft: dict) -> dict | None:
    """UC-03: approve, edit or reject the drafted customer information request."""
    st.markdown("#### Customer information request — approval needed")
    rail = draft.get("rail", {})
    if rail.get("passed"):
        st.success(f"Tipping-off rail passed (draft attempt {rail.get('attempts', 1)}): no suspicion, investigation, "
                   "report or legal conclusion is mentioned.")
    else:
        st.error(f"Tipping-off rail failed: {', '.join(rail.get('violations', []))}. Edit or reject it.")
    message = st.text_area("Message to the customer", draft["message"], key=f"msg-{case_id}")
    questions = st.text_area("Questions (one per line)", "\n".join(draft["questions"]), key=f"qs-{case_id}")
    approver = st.text_input("Approver ID", "INV-0002", key=f"approver-{case_id}")
    c1, c2, c3 = st.columns(3)
    action = ("approve" if c1.button("Approve", type="primary", key=f"approve-{case_id}") else
              "edit" if c2.button("Approve with my edits", key=f"edit-{case_id}") else
              "reject" if c3.button("Reject", key=f"reject-{case_id}") else None)
    if not action:
        return None
    approval = {"action": action, "approver_id": approver, "decided_at": now()}
    if action == "edit":
        approval |= {"message": message, "questions": [q.strip() for q in questions.splitlines() if q.strip()]}
    try:
        validate_approval(approval)
    except ValueError as e:
        st.error(str(e))
        return None
    return approval


def reply_form(case_id: str) -> str | None:
    """UC-04: simulate the customer's reply, which starts a follow-up investigation."""
    st.markdown("#### Customer reply")
    st.caption("The case is waiting for the customer. Simulate their reply to start a follow-up run with it as evidence.")
    text = st.text_area("Reply from the customer", key=f"reply-{case_id}",
                        placeholder="e.g. The payment was the proceeds of selling my car; the invoice is attached.")
    if st.button("Send customer reply", type="primary", key=f"send-reply-{case_id}") and text.strip():
        return text.strip()
    return None


def render_security(values: dict, case_id: str, legal_entity: str) -> None:
    """Guardrail activity for this case: injections caught, tools denied, budgets, PII, red-team probe."""
    events = values.get("security_events") or []
    usage = values.get("usage") or {}
    tokens = sum(u.get("input_tokens", 0) + u.get("output_tokens", 0) for u in usage.values())
    kinds = [e["kind"] for e in events]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Injections caught", kinds.count("injection_detected"))
    c2.metric("Tool calls denied", kinds.count("tool_denied"))
    c3.metric("Tokens used", f"{tokens:,}", help=f"Case budget {settings.case_token_budget:,}")
    c4.metric("PII values redacted", len(values.get("pii_vault") or {}) or values.get("pii_values_redacted", 0),
              help="Models only ever saw tokens such as <PERSON_3fa91c> for these values")
    if values.get("budget_exceeded"):
        st.error("Token budget exceeded: the case went to human review without further automated rework.")
    if events:
        st.dataframe(pd.DataFrame([{"time": (e.get("at") or "")[11:19], "event": e["kind"], "agent": e["agent"],
                                    "detail": e["detail"]} for e in events]), width="stretch", hide_index=True)
    else:
        st.caption("No security events for this case.")
    st.markdown("**Red-team probe**")
    st.caption("Sends forged calls to forbidden tools (close_alert, file_sar, …) through the same OPA check "
               "the agents use. Every one must be denied and logged.")
    if st.button("Run red-team probe", key=f"probe-{case_id}"):
        from sentinel.redteam import probe

        try:
            state[f"probe-{case_id}"] = asyncio.run(probe(case_id, legal_entity))
        except RuntimeError as e:
            st.warning(str(e))
    if rows := state.get(f"probe-{case_id}"):
        denied = sum(r["denied"] for r in rows)
        (st.success if denied == len(rows) else st.error)(f"{denied}/{len(rows)} forged tool calls denied by OPA")
        st.dataframe(pd.DataFrame([{"agent": r["agent"], "tool": r["tool"], "result": "DENIED" if r["denied"] else
                                    "ALLOWED", "attempt": r["attempt"], "reason": r["reason"]} for r in rows]),
                     width="stretch", hide_index=True)


def issues_table(issues: list[dict]) -> str:
    """QA issues as a wrapped HTML table (a dataframe would cut the long descriptions off)."""
    rows = []
    for i in issues:
        text = i["description"]
        source = "QA critic" if text.startswith("[critic]") else "Code check"
        text = text.removeprefix("[critic]").strip()
        agent = AGENT_LABELS.get(i.get("target_agent"), i.get("target_agent") or "-")
        rows.append(f'<tr><td><span class="sn-sev {i["severity"]}">{i["severity"]}</span></td><td>{source}</td>'
                    f"<td>{agent}</td><td>{html.escape(text)}</td></tr>")
    return ('<table class="sn-issues"><tr><th style="width:90px">Severity</th><th style="width:105px">Found by</th>'
            '<th style="width:120px">Sent to</th><th>Issue</th></tr>' + "".join(rows) + "</table>")


USAGE_COLUMNS = {"seconds": "Seconds", "model_calls": "Model calls", "tool_calls": "Tool calls",
                 "input_tokens": "Input tokens", "output_tokens": "Output tokens"}


def usage_table(timed: list[tuple[str, dict]]) -> str:
    """Per-agent time and usage with a bold total row (seconds add up agent time, not wall clock)."""
    def cells(values: dict) -> str:
        return "".join(f'<td class="num">{values.get(k, 0):,.1f}</td>' if k == "seconds" else
                       f'<td class="num">{int(values.get(k, 0)):,}</td>' for k in USAGE_COLUMNS)

    total = {k: sum(u.get(k, 0) for _, u in timed) for k in USAGE_COLUMNS}
    rows = "".join(f"<tr><td>{label}</td>{cells(u)}</tr>" for label, u in timed)
    head = "".join(f'<th class="num">{h}</th>' for h in USAGE_COLUMNS.values())
    return (f'<table class="sn-issues"><tr><th>Agent</th>{head}</tr>{rows}'
            f'<tr class="total"><td>Total</td>{cells(total)}</tr></table>')


def for_display(values: dict) -> dict:
    """Agents' outputs carry PII tokens; investigators see the real values unless they choose not to."""
    if not state.get("show_pii", True):
        return values
    vault = PiiVault(mapping=values.get("pii_vault"))
    return {**values, **{k: vault.restore(values[k]) for k in ("narrative", "findings", "info_request",
                                                                  "qa_issues", "follow_up") if values.get(k)}}


def render_case(alert: dict, status: str | None, values: dict, packet: dict | None, events: list[dict],
                on_decision, decision_note: str | None = None, on_approval=None, on_reply=None) -> None:
    raw_values = values
    values = for_display(values)
    if packet and packet.get("kind") == "approval":
        packet = {**packet, "draft": for_display({"info_request": packet["draft"], "pii_vault":
                                                   raw_values.get("pii_vault")})["info_request"]}
    narrative = values.get("narrative") or {}
    findings = values.get("findings", {})
    typology = findings.get("typology") or {}
    critic = findings.get("qa")
    evidence = {e["id"]: e for e in values.get("evidence", [])}
    case_heading(alert["case_id"], alert.get("scenario_name", ""), status)
    # Outcome first (wide cards for long codes), then the run statistics
    c1, c2, c3 = st.columns([1, 2, 1.2])  # reason code gets the room: the longest is FP_KNOWN_BUSINESS_PATTERN
    c1.metric("Recommendation", typology.get("recommendation") or narrative.get("recommendation", "-"))
    c2.metric("Reason code", typology.get("reason_code") or narrative.get("reason_code", "-"))
    rounds = values.get("qa_rounds", 0)
    c3.metric("QA", ("⚠ critic unavailable" if critic.get("error") else "✓ passed" if critic["passed"] else
                     "✗ issues raised") if critic else "code checks",
              help=f"{rounds} QA round(s); the critic's findings are listed in the Review tab")
    c4, c5, c6, c7, c8 = st.columns(5)
    c4.metric("Risk score", typology.get("risk_score", "-"))
    c5.metric("Lane", values.get("tier", "-"))
    c6.metric("Evidence items", len(evidence))
    c7.metric("Investigation time", duration(investigation_seconds(events)),
              help="From the start of the run (or follow-up run) until it paused for a human")
    tokens = sum(u.get("input_tokens", 0) + u.get("output_tokens", 0) for u in (raw_values.get("usage") or {}).values())
    c8.metric("Tokens used", f"{tokens:,}")

    review, typology_tab, network_tab, security_tab, findings_tab, evidence_tab, trace_tab, raw_tab = st.tabs(
        ["Review", "Typology & policy", "Network", "Security", "Findings", "Evidence", "Trace", "Raw state"])
    with review:
        if follow_up := values.get("follow_up"):
            st.info(f"**Follow-up round {follow_up['round']}** after a request for information. "
                    f"Customer reply: “{follow_up['reply_text']}”")
        if (security := raw_values.get("security_events")) and any(e["kind"] == "injection_detected" for e in security):
            st.warning("A record in this case contained instruction-like text. It was fenced off as data and did "
                       "not change what the agents could do (see the Security tab).")
        serious = [i for i in values.get("qa_issues", []) if i["severity"] in ("blocker", "major")]
        notes = [i for i in values.get("qa_issues", []) if i["severity"] == "minor"]
        if serious:
            st.markdown(f"**⚠️ Unresolved QA issues ({len(serious)})**")
            st.markdown(issues_table(serious), unsafe_allow_html=True)
        if critic:
            checks = " ".join(f"{'✅' if ok else '❌'} {name.replace('_', ' ')}" for name, ok in critic["checks"].items())
            st.caption(f"QA critic ({critic['model'].split('.')[-1].split(':')[0]}): "
                       + (f"unavailable ({critic['error']})" if critic.get("error") else checks))
        if notes:
            with st.expander(f"QA notes ({len(notes)} minor)"):
                st.markdown(issues_table(notes), unsafe_allow_html=True)
        st.markdown(f"**Summary.** {narrative.get('summary', '')}")
        st.markdown("**Claims**")
        for i, claim in enumerate(narrative.get("claims", []), 1):
            bad = [eid for eid in claim["evidence_ids"] if eid not in evidence]
            st.markdown(f"{i}. {claim['text']}  " + " ".join(f"`{eid}`" for eid in claim["evidence_ids"]))
            if bad:
                st.error(f"Unknown evidence IDs: {bad}")
            with st.expander("Evidence", expanded=False):
                for eid in claim["evidence_ids"]:
                    e = evidence.get(eid)
                    st.markdown(f"- `{eid}` — {e['summary'] if e else '**not in evidence**'}")
        if narrative.get("open_questions"):
            st.markdown("**Open questions**")
            st.markdown("\n".join(f"- {q}" for q in narrative["open_questions"]))
        request = values.get("info_request")
        if request and not (packet and packet.get("kind") == "approval"):
            with st.expander(f"Customer information request · {request['status'].replace('_', ' ')}",
                             expanded=request["status"] != "rejected"):
                st.markdown(request["message"] + "\n\n" + "\n".join(f"- {q}" for q in request["questions"]))
                if request.get("approver_id"):
                    st.caption(f"Decided by {request['approver_id']}")
        st.divider()
        if decision_note:
            st.info(decision_note)
        elif packet and packet.get("kind") == "approval":
            if on_approval and (approval := approval_form(alert["case_id"], packet["draft"])):
                on_approval(approval)
        elif values.get("decision"):
            d = values["decision"]
            st.success(f"Decision recorded: **{d['action']}** ({d['reason_code']}) by {d['investigator_id']}"
                       + ("" if d.get("agree_with_recommendation") else " — overrides the recommendation"))
            if d["action"] == "request_info" and on_reply and (reply := reply_form(alert["case_id"])):
                on_reply(reply)
        elif packet:
            if decision := decision_form(alert["case_id"], packet):
                on_decision(decision)
        else:
            st.error("The run ended without reaching human review.")
    with typology_tab:
        render_typology(typology, evidence)
    with network_tab:
        render_network(findings.get("network"), values.get("tier"), evidence, alert["customer_id"])
    with security_tab:
        render_security(raw_values, alert["case_id"], alert.get("legal_entity", "UK"))
    with findings_tab:
        usage = raw_values.get("usage") or {}
        timed = [(label, usage[a]) for a, label in AGENT_LABELS.items() if usage.get(a, {}).get("seconds")]
        st.markdown(f"**Time and usage by agent** · investigation {duration(investigation_seconds(events))} "
                    "wall clock (KYC, Transactions and Screening run in parallel)")
        if timed:
            st.markdown(usage_table(timed), unsafe_allow_html=True)
        else:
            st.caption("No agent timings in this run: they are recorded by runs started after the console and "
                       "worker were restarted on the current code.")
        for agent in ("triage", "kyc", "txn", "screening", "network", "typology", "qa"):
            if agent in findings:
                seconds = usage.get(agent, {}).get("seconds")
                took = f" · ⏱ {seconds:.0f}s" if seconds else " · rules, no model" if agent == "triage" else ""
                with st.expander(f"{AGENT_LABELS.get(agent, agent.title())}{took}", expanded=False):
                    st.json(findings[agent])
    with evidence_tab:
        st.dataframe(pd.DataFrame(values.get("evidence", []), columns=["id", "agent", "source", "summary"]),
                     width="stretch", hide_index=True)
    with trace_tab:
        earlier = len(events) - len(current_run(events))
        include = earlier and st.toggle(f"Include earlier runs ({earlier} events)", key=f"trace-all-{alert['case_id']}")
        st.dataframe(events_frame(events if include else current_run(events)), width="stretch", hide_index=True)
        st.markdown("**Prompt / model versions**")
        st.json(values.get("versions", {}))
    with raw_tab:
        st.json(raw_values)


def render_typology(typology: dict, evidence: dict) -> None:
    if not typology:
        st.info("No typology assessment yet.")
        return
    st.markdown(f"**Recommendation:** `{typology.get('recommendation')}` ({typology.get('reason_code')}) · "
                f"risk score **{typology.get('risk_score')}**/100")
    st.markdown(f"**Rationale.** {typology.get('rationale', '')}")
    matches = typology.get("typologies", [])
    if matches:
        st.dataframe(pd.DataFrame([{"typology": m["code"], "confidence": f"{m['confidence']:.0%}",
                                    "rationale": m["rationale"], "evidence": ", ".join(m["evidence_ids"]),
                                    "policy": ", ".join(m["policy_refs"])} for m in matches]),
                     width="stretch", hide_index=True)
    refs = sorted(set(typology.get("policy_refs", [])) | {r for m in matches for r in m["policy_refs"]})
    if refs:
        st.markdown("**Policy sections relied on**")
        for ref in refs:
            e = evidence.get(ref)
            st.markdown(f"- `{ref}` — {e['summary'] if e else '**not in evidence**'}")


LEGEND = ('<div class="sn-legend">Customers by mule score:<i style="background:#F8B4B4"></i>0.6 or more'
          '<i style="background:#FCE7B2"></i>0.3–0.6<i style="background:#E2E8F0"></i>below 0.3'
          '<i style="background:#DBEAFE"></i>shared device · dashed line = transfer · bold outline = this case</div>')


def render_network_diagram(customer_id: str) -> None:
    """Drawn from the graph itself (not from the agent's summary), so it always shows the real links."""
    from sentinel.graphdb import network_dot

    try:
        dot = network_dot(customer_id)
    except Exception as e:  # noqa: BLE001 - graph down: the findings below still show
        st.warning(f"Network diagram unavailable ({type(e).__name__}).")
        return
    if not dot:
        return
    if dot.count("shape=ellipse") == 1 and "shape=box" not in dot:  # only the case customer: nothing to draw
        st.caption(f"{customer_id} has no linked customers or shared devices within 2 hops.")
        return
    st.graphviz_chart(dot, width="content")  # natural size: stretching blows small graphs up
    st.markdown(LEGEND, unsafe_allow_html=True)


def render_network(network: dict | None, tier: str | None, evidence: dict, customer_id: str) -> None:
    if not network:
        st.info("Fast lane: network analysis runs only in the full lane." if tier == "fast"
                else "No network findings yet.")
        return
    render_network_diagram(customer_id)
    label = {"isolated": "Isolated", "benign_links": "Benign links",
             "mule_network_suspected": "Mule network suspected"}.get(network["assessment"], network["assessment"])
    c1, c2, c3 = st.columns(3)
    c1.metric("Assessment", label)
    c2.metric("Mule score", network.get("mule_score") if network.get("mule_score") is not None else "-")
    c3.metric("Related customers", len(network.get("related_parties", [])))
    st.markdown(f"**Summary.** {network.get('summary', '')}")
    if network.get("related_parties"):
        st.dataframe(pd.DataFrame([{"customer": p["customer_id"], "relationship": p["relationship"],
                                    "mule score": p.get("mule_score"), "evidence": ", ".join(p["evidence_ids"])}
                                   for p in network["related_parties"]]), width="stretch", hide_index=True)
    for device in network.get("shared_devices", []):
        st.markdown(f"- {device['description']}  " + " ".join(f"`{eid}`" for eid in device["evidence_ids"]))


def render_queue(rows: list[dict], key: str, note: str) -> None:
    """Shared work-queue table; selecting a row opens the case in the Case tab."""
    statuses = st.multiselect("Status", ALL_STATUSES, default=ALL_STATUSES, key=f"{key}-filter")
    rows = [r for r in rows if r["status"] in statuses]
    queue = pd.DataFrame([{"status": f"{STATUS_ICON.get(r['status'], '')} {r['status']}", "case": r["case_id"],
                           "scenario": r["scenario"], "typology": r.get("typology") or "", "entity": r["legal_entity"],
                           "updated": r["updated"]} for r in rows],
                         columns=["status", "case", "scenario", "typology", "entity", "updated"])
    st.caption(f"{len(queue)} case(s) · {note} Select a row to open it in the Case tab.")
    picked = st.dataframe(queue, width="stretch", hide_index=True, on_select="rerun",
                          selection_mode="single-row", key=key)
    if picked.selection.rows:
        state.case = queue.iloc[picked.selection.rows[0]]["case"]
        st.success(f"Opened {state.case} in the Case tab.")


def render_stream(events: list[dict], note: str) -> None:
    st.caption(f"{len(events)} event(s) · {note} Newest first.")
    st.dataframe(events_frame(events[-300:]), width="stretch", hide_index=True)


# --- Direct mode --------------------------------------------------------------------------------
@st.cache_resource
def get_graph():
    """One compiled graph + in-memory checkpointer for the life of the Streamlit server."""
    return compile_graph()


def emit(case_id: str, event_type: str, node: str | None = None, detail: str | None = None,
         data_: dict | None = None) -> None:
    """Direct-mode case events, in the same shape the Kafka worker publishes."""
    state.setdefault("direct_events", []).append(
        {"case_id": case_id, "type": event_type, "node": node, "detail": detail, "data": data_, "at": now()})


async def stream_graph(payload, config: dict, on_update) -> object:
    start = time.perf_counter()
    async for update in get_graph().astream(payload, config, stream_mode="updates"):
        for node, out in update.items():
            if node != "__interrupt__":
                on_update(node, out or {}, time.perf_counter() - start)
    return await get_graph().aget_state(config)


def execute(case_id: str, payload, config: dict, label: str):
    """Run (or resume) the graph, showing node-by-node progress and a running clock; returns the final
    snapshot. The graph runs on a background thread so this script thread can tick the clock; only this
    thread touches Streamlit, so progress comes back through a queue."""
    updates: queue.Queue = queue.Queue()
    outcome: dict = {}

    def run() -> None:
        try:
            outcome["snapshot"] = asyncio.run(stream_graph(payload, config, lambda *u: updates.put(u)))
        except Exception as e:  # noqa: BLE001 - reported below on the script thread
            outcome["error"] = e

    def show(node: str, out: dict, elapsed: float) -> None:
        lines = describe_update(node, out) or [f"[{node}]"]
        emit(case_id, "node_completed", node, "; ".join(l.split("]", 1)[-1].strip() for l in lines))
        for event in out.get("security_events") or []:
            emit(case_id, "security_event", node, f"{event['kind']}: {event['detail']}", event.get("data"))
            st.write(f"`{elapsed:5.1f}s` 🛡️ {event['kind']}: {event['detail']}")
        for line in lines:
            st.write(f"`{elapsed:5.1f}s` {line}")

    start = time.perf_counter()
    with st.status(label, expanded=True) as status:
        clock = st.empty()
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        last = "starting"
        while worker.is_alive() or not updates.empty():
            while not updates.empty():
                node, out, elapsed = updates.get()
                show(node, out, elapsed)
                last = AGENT_LABELS.get(node, node.replace("_", " "))
            elapsed = time.perf_counter() - start
            clock.markdown(running_banner(elapsed, f"still working · last completed step: {last}"),
                           unsafe_allow_html=True)
            status.update(label=f"{label} · {duration(elapsed)}")
            worker.join(timeout=0.5)
        clock.empty()
        total = duration(time.perf_counter() - start)
        if e := outcome.get("error"):  # surface model/tool errors in the UI instead of a stack trace page
            emit(case_id, "error", detail=f"{type(e).__name__}: {str(e)[:200]}")
            state.direct[case_id]["status"] = "error"
            status.update(label=f"Failed after {total}: {type(e).__name__}", state="error")
            st.exception(e)
            st.stop()
        status.update(label=f"{label}: done in {total}", state="complete", expanded=False)
    return outcome["snapshot"]


def direct_run(alert: dict) -> None:
    case_id = alert["case_id"]
    thread_id = f"{case_id}:ui-{uuid.uuid4().hex[:6]}"  # fresh thread so reruns don't share state
    state.setdefault("direct", {})[case_id] = {"thread_id": thread_id, "alert": alert, "status": "in_progress",
                                               "values": {}, "packet": None, "updated": now()}
    emit(case_id, "case_started", data_={"scenario": alert["scenario_code"]})
    snap = execute(case_id, initial_state(alert), run_config(thread_id), f"Investigating {case_id}")
    record_pause(case_id, state.direct[case_id], snap)


def record_pause(case_id: str, run: dict, snap) -> None:
    """Where the run is waiting: the approval of a customer request (UC-03) or the disposition."""
    packet = snap.interrupts[0].value if snap.interrupts else None
    kind = (packet or {}).get("kind")
    status = "awaiting_approval" if kind == "approval" else "awaiting_review" if packet else "error"
    run.update(values=snap.values, packet=packet, status=status, updated=now())
    if kind == "approval":
        emit(case_id, "awaiting_approval", data_={"rail_passed": packet["draft"]["rail"]["passed"]})
    elif packet:
        emit(case_id, "awaiting_review", data_={"recommendation": packet["recommendation"],
                                                "reason_code": packet["reason_code"], "tier": packet["tier"]})


def direct_case_view(case_id: str, alert: dict, run_now: bool) -> None:
    if run_now:
        # Heading first, so the progress box replaces whatever was below it on the previous page.
        case_heading(case_id, alert.get("scenario_name", ""), "in_progress")
        direct_run(alert)
        st.rerun()  # redraw everything, including the Work queue and Event stream tabs drawn before this one
    run = state.get("direct", {}).get(case_id)
    if not run or not run["values"]:
        case_heading(case_id, alert.get("scenario_name", ""), None)
        st.info("Not run yet in this session. Press **Run investigation** in the sidebar.")
        return

    def on_decision(decision: dict) -> None:
        snap = execute(case_id, Command(resume=decision), run_config(run["thread_id"]), "Recording decision")
        emit(case_id, "decision_applied", data_={"action": decision["action"], "reason_code": decision["reason_code"],
                                                 "investigator_id": decision["investigator_id"]})
        if snap.interrupts:  # escalated: now waiting at the next level (BR-16)
            record_pause(case_id, run, snap)
        else:
            run.update(values=snap.values, packet=None, status=STATUS_AFTER_DECISION[decision["action"]], updated=now())
        st.rerun()

    def on_approval(approval: dict) -> None:
        snap = execute(case_id, Command(resume=approval), run_config(run["thread_id"]), "Recording approval")
        emit(case_id, "approval_applied", data_={"action": approval["action"], "approver_id": approval["approver_id"]})
        record_pause(case_id, run, snap)
        st.rerun()

    def on_reply(text: str) -> None:
        follow = follow_up_state(run["values"], run["thread_id"], text, now())
        run["thread_id"] = f"{run['thread_id']}:r{follow['follow_up']['round']}"
        emit(case_id, "follow_up_started", data_={"thread": run["thread_id"]})
        snap = execute(case_id, follow, run_config(run["thread_id"]), "Follow-up investigation with the customer reply")
        record_pause(case_id, run, snap)
        st.rerun()

    events = [e for e in state.get("direct_events", []) if e["case_id"] == case_id]
    render_case(run["alert"], run["status"], run["values"], run["packet"], events, on_decision,
                on_approval=on_approval, on_reply=on_reply)


def direct_mode(alert: dict, run_now: bool) -> None:
    case_tab, queue_tab, stream_tab = st.tabs(["Case", "Work queue", "Event stream"])
    with queue_tab:
        truth = data.ground_truth()
        rows = [{"case_id": cid, "status": r["status"], "scenario": r["alert"]["scenario_name"],
                 "typology": truth.get(cid, {}).get("typology"), "legal_entity": r["alert"]["legal_entity"],
                 "updated": r["updated"][11:19]}
                for cid, r in sorted(state.get("direct", {}).items(), key=lambda kv: kv[1]["updated"], reverse=True)]
        render_queue(rows, "direct-queue", "Cases run in this browser session (in-memory).")
    with stream_tab:
        render_stream(state.get("direct_events", []), "Events from this session's runs.")
    with case_tab:
        if state.case == alert["case_id"]:
            case_alert = alert
        else:  # opened from the work queue
            case_alert = state.get("direct", {}).get(state.case, {}).get("alert") or data.get_alert(state.case) or alert
        direct_case_view(case_alert["case_id"], case_alert, run_now)


# --- Kafka mode ---------------------------------------------------------------------------------
def reachable(host_port: str) -> bool:
    host, _, port = host_port.rpartition(":")
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host or "localhost", int(port))) == 0


def pg_host_port() -> str:
    return settings.pg_dsn.rsplit("@", 1)[-1].split("/", 1)[0]


def load_snapshot(case_id: str) -> tuple[dict, dict | None]:
    """Case state of the current run thread from the Postgres checkpointer (no agents run here)."""
    with sync_checkpointer() as saver:
        snap = build_graph().compile(checkpointer=saver).get_state(run_config(case_thread(case_id)))
    return snap.values, (snap.interrupts[0].value if snap.interrupts else None)


def publish_approval(case_id: str, approval: dict) -> None:
    asyncio.run(kafka.publish(DECISIONS_TOPIC, [ApprovalEvent(case_id=case_id, **approval)]))


def publish_reply(case_id: str, text: str) -> None:
    asyncio.run(kafka.publish(FOLLOWUPS_TOPIC, [FollowUpEvent(case_id=case_id, reply_text=text)]))


def publish_alert(alert: dict) -> None:
    asyncio.run(kafka.publish(ALERTS_TOPIC, [AlertEvent.model_validate(alert)]))


def publish_decision(case_id: str, decision: dict) -> None:
    asyncio.run(kafka.publish(DECISIONS_TOPIC, [DecisionEvent(case_id=case_id, **decision)]))


def mark_published(case_id: str) -> None:
    state.setdefault("published_at", {})[case_id] = now()


@st.fragment(run_every=2)
def live_progress(case_id: str, seen_status: str | None, waiting_for: str) -> None:
    """Poll status and events every 2 s; rerun the page once the worker moves the case on."""
    show_progress(case_id, case_status(case_id), asyncio.run(kafka.read_events(case_id)), seen_status, waiting_for)


def show_progress(case_id: str, status: str | None, events: list[dict], seen_status: str | None,
                  waiting_for: str) -> None:
    start = latest_start(events) if status == "in_progress" else None
    since = start["at"] if start else state.get("published_at", {}).get(case_id)
    steps = [e for e in events if e["type"] == "node_completed" and (not start or e["at"] >= start["at"])]
    detail = waiting_for + (f" · last completed step: {AGENT_LABELS.get(steps[-1]['node'], steps[-1]['node'])}"
                            if steps and start else "")
    st.markdown(running_banner(seconds_between(since, now()) if since else 0, detail), unsafe_allow_html=True)
    st.dataframe(events_frame(current_run(events)), width="stretch", hide_index=True)
    if status != seen_status:
        st.rerun(scope="app")


def kafka_case_view(case_id: str) -> None:
    alert = data.get_alert(case_id) or {"case_id": case_id, "scenario_name": ""}
    status = case_status(case_id)
    pending = state.setdefault("pending", {})

    b1, b2, _ = st.columns([1.4, 1, 4])
    if status and status != "new" and b1.button("Re-publish alert", help="Should be ignored as a duplicate"):
        publish_alert(alert)
        st.toast("Alert re-published; the worker should report duplicate_ignored")
    if status and b2.button("Reset case", help="Dev only: delete the checkpoint so the alert can run again"):
        reset_case(case_id)
        pending.pop(case_id, None)
        st.rerun()

    if status in (None, "new", "in_progress", "error"):
        case_heading(case_id, alert.get("scenario_name", ""), status)
    if status in (None, "new"):
        if case_id in pending:
            live_progress(case_id, status, "waiting for a worker to pick up the alert")
        else:
            st.info("Not started. Use **Publish alert to Kafka** in the sidebar.")
        return
    if status == "in_progress":
        live_progress(case_id, status, "the worker is investigating")
        return
    if status == "error":
        st.error("The worker could not process this case; see the events below and the DLQ topic.")
        st.dataframe(events_frame(asyncio.run(kafka.read_events(case_id))), width="stretch", hide_index=True)
        return

    values, packet = load_snapshot(case_id)
    events = asyncio.run(kafka.read_events(case_id))
    waiting = {("awaiting_review", "decision"): "Decision published to Kafka; waiting for the worker to apply it.",
               ("awaiting_approval", "approval"): "Approval published to Kafka; waiting for the worker to apply it.",
               ("info_requested", "reply"): "Customer reply published to Kafka; waiting for the worker to start "
                                            "the follow-up investigation."}
    sent = pending.get(case_id)
    if isinstance(sent, tuple) and sent[1] != status:  # the worker has moved the case on since we published
        pending.pop(case_id, None)
        sent = None
    note = waiting.get((status, sent[0])) if isinstance(sent, tuple) else None
    if status in TERMINAL and not values.get("decision"):
        note = f"Case is {status}."

    def published(kind: str) -> None:
        pending[case_id] = (kind, status)  # remembered until the status changes
        mark_published(case_id)
        st.rerun()

    def on_decision(decision: dict) -> None:
        publish_decision(case_id, decision)
        published("decision")

    def on_approval(approval: dict) -> None:
        publish_approval(case_id, approval)
        published("approval")

    def on_reply(text: str) -> None:
        publish_reply(case_id, text)
        published("reply")

    render_case(alert, status, values, packet, events, on_decision, note, on_approval=on_approval, on_reply=on_reply)
    if note and status in ("awaiting_review", "awaiting_approval", "info_requested"):
        live_progress(case_id, status, note)
    if status in TERMINAL:
        if st.button("Send another decision", help="Should be ignored: the case is no longer waiting for review"):
            publish_decision(case_id, {"action": "close", "reason_code": "FP_DATA_ERROR",
                                       "investigator_id": "INV-0001"})
            st.toast("Decision sent; the worker should report decision_ignored")


def kafka_mode() -> None:
    missing = [name for name, hp in (("Kafka", settings.kafka_bootstrap), ("Postgres", pg_host_port()))
               if not reachable(hp)]
    if missing:
        st.error(f"{' and '.join(missing)} not reachable. Start the stack with `start_sentinel.bat` "
                 "or `docker compose up -d --build`.")
        st.stop()
    case_tab, queue_tab, stream_tab = st.tabs(["Case", "Work queue", "Event stream"])
    with queue_tab:
        rows = [{**r, "updated": r["updated_at"].strftime("%H:%M:%S")} for r in list_cases() if r["status"] != "new"]
        render_queue(rows, "kafka-queue", "Cases the workers have touched (from Postgres).")
    with stream_tab:
        if st.button("Refresh events"):
            pass
        render_stream(asyncio.run(kafka.read_events()), "From the aml.case-events.v1 topic.")
    with case_tab:
        kafka_case_view(state.case)


# --- API mode -----------------------------------------------------------------------------------
# Everything goes through the Sentinel API with a signed-in test user's token: the same calls the
# React workbench will make. Roles change what you may do (BR-08, UC-03, UC-05) and see (FR-109).
API_ROLES = {"l1": "L1 analyst", "l2": "L2 investigator", "mlro": "MLRO", "qa": "QA reviewer", "admin": "Administrator"}
QA_RUBRIC = {"evidence_complete": "Evidence complete", "citations_accurate": "Citations accurate",
             "recommendation_sound": "Recommendation sound", "narrative_clear": "Narrative clear"}


@st.cache_data(ttl=3000, show_spinner=False)  # Cognito access tokens last an hour
def api_token(role: str) -> str:
    from sentinel.api.auth import token_for

    return token_for(role)


def api_call(method: str, path: str, **kwargs):
    import httpx

    headers = {"Authorization": f"Bearer {api_token(state.get('api_role', 'l2'))}"}
    return httpx.request(method, f"{settings.api_url}{path}", headers=headers, timeout=30, **kwargs)


def api_case(case_id: str) -> dict | None:
    resp = api_call("GET", f"/cases/{case_id}")
    return resp.json() if resp.status_code == 200 else None


def api_submit(case_id: str, status: str, path: str, body: dict, kind: str) -> None:
    """POST a change; on success remember it as pending until the case leaves `status`, read before the
    POST (the worker often applies the change before a read after it could see the old status).
    Otherwise keep the API's refusal to show."""
    resp = api_call("POST", path, json=body)
    if resp.status_code in (201, 202):
        state.setdefault("api_pending", {})[case_id] = (kind, status)
        mark_published(case_id)
    else:
        state["api_flash"] = f"API refused ({resp.status_code}): {resp.json().get('detail')}"
    st.rerun()


@st.fragment(run_every=2)
def api_live_progress(case_id: str, seen_status: str | None, waiting_for: str) -> None:
    case = api_case(case_id)
    status = case["case"]["status"] if case else None
    events = api_call("GET", f"/cases/{case_id}/events").json() if case else []
    show_progress(case_id, status, events, seen_status, waiting_for)


def api_case_view(case_id: str) -> None:
    if flash := state.pop("api_flash", None):
        st.error(flash)
    case = api_case(case_id)
    alert = (case or {}).get("case", {}).get("alert") or data.get_alert(case_id) or {"case_id": case_id,
                                                                                   "scenario_name": ""}
    status = case["case"]["status"] if case else None
    pending = state.setdefault("api_pending", {})
    if status in (None, "new", "in_progress", "error"):
        case_heading(case_id, alert.get("scenario_name", ""), status)
    if status in (None, "new"):
        if case_id in pending:
            api_live_progress(case_id, status, "waiting for a worker to pick up the alert")
        else:
            st.info("Not started. Use **Submit alert via API** in the sidebar.")
        return
    if status == "in_progress":
        api_live_progress(case_id, status, "the worker is investigating")
        return
    events = api_call("GET", f"/cases/{case_id}/events").json()
    if status == "error":
        st.error("The worker could not process this case; see the events below.")
        st.dataframe(events_frame(events), width="stretch", hide_index=True)
        return

    values = case["state"]
    packet = {**case["waiting_for"], **(case["recommendation"] or {})} if case["waiting_for"] else None
    sent = pending.get(case_id)
    if isinstance(sent, tuple) and sent[1] != status:
        pending.pop(case_id, None)
        sent = None
    note = {"decision": "Decision accepted by the API; waiting for the worker to apply it.",
            "approval": "Approval accepted by the API; waiting for the worker to apply it.",
            "reply": "Reply accepted by the API; waiting for the worker to start the follow-up."}.get(
        sent[0]) if isinstance(sent, tuple) else None

    def on_decision(decision: dict) -> None:
        api_submit(case_id, status, f"/cases/{case_id}/decision",
                   {k: decision.get(k) for k in ("action", "reason_code", "narrative_edits")}, "decision")

    def on_approval(approval: dict) -> None:
        body = {k: approval[k] for k in ("action", "message", "questions") if approval.get(k)}
        api_submit(case_id, status, f"/cases/{case_id}/approval", body, "approval")

    def on_reply(text: str) -> None:
        api_submit(case_id, status, f"/cases/{case_id}/reply", {"reply_text": text}, "reply")

    render_case(alert, status, values, packet, events, on_decision, note, on_approval=on_approval, on_reply=on_reply)
    if note:
        api_live_progress(case_id, status, note)


def render_qa_review() -> None:
    """UC-05: a QA reviewer samples decided cases and scores them against the rubric."""
    resp = api_call("GET", "/qa/sample", params={"n": 10})
    if resp.status_code == 403:
        st.info("QA review needs the **QA reviewer** role: switch role in the sidebar.")
        return
    sample = resp.json()
    if not sample:
        st.success("Nothing left to review: every decided case has your label.")
        return
    picked = st.selectbox("Sampled case", [r["case_id"] for r in sample],
                          format_func=lambda c: next(f"{c} · {r['status']} · {r['scenario']}"
                                                     for r in sample if r["case_id"] == c))
    case = api_case(picked) or {}
    narrative = (case.get("state") or {}).get("narrative") or {}
    decision = (case.get("state") or {}).get("decision") or {}
    st.markdown(f"**Recommendation:** `{(case.get('recommendation') or {}).get('recommendation')}` · "
                f"**Decision:** `{decision.get('action')}` ({decision.get('reason_code')}) by "
                f"{decision.get('investigator_id')}")
    st.markdown(f"**Summary.** {narrative.get('summary', '')}")
    with st.form(f"qa-{picked}"):
        cols = st.columns(len(QA_RUBRIC))
        scores = {key: col.slider(label, 1, 5, 3) for col, (key, label) in zip(cols, QA_RUBRIC.items())}
        correct = st.toggle("The final decision was correct", value=True)
        comment = st.text_area("Comment (optional)")
        if st.form_submit_button("Save label", type="primary"):
            body = {**scores, "decision_correct": correct, "comment": comment or None}
            r = api_call("POST", f"/cases/{picked}/qa-label", json=body)
            (st.success if r.status_code == 201 else st.error)(
                "Label saved." if r.status_code == 201 else f"API refused ({r.status_code}): {r.json().get('detail')}")


def api_mode() -> None:
    try:
        me = api_call("GET", "/me")
    except Exception as e:  # noqa: BLE001 - API down or no token: say how to fix it
        st.error(f"Sentinel API not reachable at {settings.api_url} ({type(e).__name__}: {e}). Start it with "
                 "`docker compose up -d api` (or `sentinel api`), and check the Cognito settings in .env.")
        st.stop()
    if me.status_code != 200:
        st.error(f"Sign-in failed ({me.status_code}): {me.json().get('detail')}")
        st.stop()
    case_tab, queue_tab, stream_tab, qa_tab = st.tabs(["Case", "Work queue", "Event stream", "QA review"])
    with queue_tab:
        rows = [{**r, "typology": "", "updated": str(r["updated_at"])[11:19]}
                for r in api_call("GET", "/cases").json() if r["status"] != "new"]
        render_queue(rows, "api-queue", "From GET /cases.")
    with stream_tab:
        render_stream(api_call("GET", f"/cases/{state.case}/events").json() if api_case(state.case) else [],
                      f"Events of {state.case} from GET /cases/{{id}}/events.")
    with qa_tab:
        render_qa_review()
    with case_tab:
        api_case_view(state.case)


# --- Page ---------------------------------------------------------------------------------------
def select_case() -> None:
    state.case = state.case_labels[state.case_choice]


with st.sidebar:
    mode = st.radio("Mode", [DIRECT, KAFKA, API], help="Kafka and API modes need the Docker stack and "
                                                        "`sentinel worker all`; API mode also the `api` service")
    if mode == API:
        st.selectbox("Signed in as", list(API_ROLES), format_func=lambda r: f"{API_ROLES[r]} ({r})", index=1,
                     key="api_role", help=f"Test users in {'Cognito' if settings.auth_mode == 'cognito' else 'dev mode'}"
                                          "; the role decides what the API lets you do and see")
    st.toggle("Show customer data", value=True, key="show_pii",
              help="Agents only ever see PII as tokens such as <PERSON_3fa91c>. Switch off to see exactly what "
                   "the models saw.")
    st.subheader("Select alert")
    truth = data.ground_truth()
    source = st.radio("Source", ["Data backend", "Fixture file"], horizontal=True)
    if source == "Data backend":
        alerts = {a["case_id"]: a for a in data.list_alerts()}
        state.case_labels = {f"{cid} · {truth.get(cid, {}).get('typology', '?')}": cid for cid in alerts}
        alert = alerts[state.case_labels[st.selectbox("Case", list(state.case_labels), key="case_choice",
                                                      on_change=select_case)]]
    else:
        choice = st.selectbox("Fixture", [f.name for f in sorted(ALERT_DIR.glob("*.json"))])
        uploaded = st.file_uploader("…or upload an alert JSON", type="json")
        alert = json.load(uploaded) if uploaded else json.loads((ALERT_DIR / choice).read_text(encoding="utf-8"))
    state.setdefault("case", alert["case_id"])
    with st.expander("Alert payload"):
        st.json(alert)
    if expected := truth.get(alert["case_id"]):
        with st.expander("Expected outcome (ground truth, never shown to agents)"):
            st.json(expected)
    run_now = False
    if mode == DIRECT:
        if st.button("▶ Run investigation", type="primary", width="stretch"):
            state.case, run_now = alert["case_id"], True
    elif mode == API:
        if st.button("📨 Submit alert via API", type="primary", width="stretch"):
            resp = api_call("POST", "/cases", json=alert)
            state.case = alert["case_id"]
            if resp.status_code == 202:
                state.setdefault("api_pending", {})[alert["case_id"]] = "alert"
                mark_published(alert["case_id"])
                st.toast(f"POST /cases accepted {alert['case_id']}")
            else:
                state["api_flash"] = f"API refused ({resp.status_code}): {resp.json().get('detail')}"
        if st.button("Open this case", width="stretch"):
            state.case = alert["case_id"]
        st.caption(f"API `{settings.api_url}` · the worker (`sentinel worker all`) runs the agents.")
    else:
        if st.button("📨 Publish alert to Kafka", type="primary", width="stretch"):
            publish_alert(alert)
            state.case = alert["case_id"]
            state.setdefault("pending", {})[alert["case_id"]] = "alert"
            mark_published(alert["case_id"])
            st.toast(f"Published {alert['case_id']} to {ALERTS_TOPIC}")
        if st.button("Open this case", width="stretch"):
            state.case = alert["case_id"]
        st.caption("The worker (`sentinel worker all`) processes alerts and decisions.")
    st.markdown("**Configuration**")
    config_rows = [("Region", settings.aws_region), *((AGENT_LABELS[a], getattr(settings, f"model_{a}")) for a in
                   ("kyc", "txn", "screening", "network", "typology", "narrative", "qa")),
                   ("Customer request", settings.model_narrative), ("Embeddings", settings.model_embedding),
                   ("Kafka", settings.kafka_bootstrap)]
    st.markdown("| | |\n|---|---|\n" + "\n".join(f"| {k} | `{v}` |" for k, v in config_rows))

header(mode)
if mode == DIRECT:
    direct_mode(alert, run_now)
elif mode == API:
    api_mode()
else:
    kafka_mode()
st.markdown('<div class="sn-footer">Synthetic data only · agents recommend, investigators decide · '
            'proof of concept</div>', unsafe_allow_html=True)
