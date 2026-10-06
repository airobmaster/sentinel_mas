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
import json
import socket
import sys
import time
import uuid
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from langgraph.types import Command

from sentinel import data, kafka
from sentinel.cli import describe_update
from sentinel.config import REPO_ROOT, settings
from sentinel.events import ALERTS_TOPIC, DECISIONS_TOPIC, AlertEvent, DecisionEvent
from sentinel.graph import build_graph, compile_graph, initial_state, run_config
from sentinel.hitl import validate_decision
from sentinel.persistence import (OPEN_STATUSES, STATUS_AFTER_DECISION, case_status, list_cases, reset_case,
                                  sync_checkpointer)
from sentinel.schemas import REASON_CODES

if sys.platform == "win32":  # psycopg's async driver cannot use the default Proactor loop on Windows
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

ALERT_DIR = REPO_ROOT / "data" / "fixtures" / "alerts"
ACTIONS = list(REASON_CODES)
STATUS_ICON = {"new": "⚪", "in_progress": "🔵", "awaiting_review": "🟡", "closed": "🟢", "escalated": "🔴",
               "info_requested": "🟣", "error": "⛔", None: "⚪"}
TERMINAL = ("closed", "escalated", "info_requested")
ALL_STATUSES = list(OPEN_STATUSES) + list(TERMINAL) + ["error"]
DIRECT, KAFKA = "Direct (in-process)", "Kafka (full stack)"

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
.sn-case-title { font-size: 22px; font-weight: 700; color: #0B2545; margin: 6px 0 2px; }
.sn-case-sub { color: #5A6B7F; font-size: 14px; margin-bottom: 12px; }
.sn-pill { display: inline-block; padding: 3px 11px; border-radius: 999px; font-size: 12.5px; font-weight: 600;
    vertical-align: middle; margin-left: 10px; }
.sn-pill.new { background: #EEF1F5; color: #4A5568; }
.sn-pill.in_progress { background: #E3F0FF; color: #0B5CAD; }
.sn-pill.awaiting_review { background: #FFF4D6; color: #8A5A00; }
.sn-pill.closed { background: #E3F6EA; color: #1E7B45; }
.sn-pill.escalated { background: #FDE7E7; color: #B42318; }
.sn-pill.info_requested { background: #F1E8FF; color: #6B3FA0; }
.sn-pill.error { background: #2D3748; color: #fff; }
[data-testid="stMetric"] { background: #F7F9FC; border: 1px solid #E3E8EF; border-radius: 12px; padding: 12px 16px; }
[data-testid="stMetricValue"] { font-size: 22px; font-weight: 700; color: #0B2545; }
.stTabs [data-baseweb="tab"] { font-weight: 600; font-size: 15px; }
[data-testid="stSidebar"] { border-right: 1px solid #E3E8EF; }
[data-testid="stAppDeployButton"] { display: none; }
.sn-footer { color: #8592A3; font-size: 12px; text-align: center; margin-top: 32px; padding-top: 12px;
    border-top: 1px solid #E9EDF2; }
</style>
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def pill(status: str | None) -> str:
    label = (status or "not started").replace("_", " ")
    return f'<span class="sn-pill {status or "new"}">{label}</span>'


def header(mode: str) -> None:
    opa = "on" if settings.opa_url else "off"
    badges = [("Mode", "Kafka" if mode == KAFKA else "Direct"), ("Data", settings.data_backend),
              ("Tools", settings.tool_mode.upper()), ("Policy (OPA)", opa), ("Model", settings.model_narrative)]
    badge_html = "".join(f'<span class="sn-badge">{k}: <b>{v}</b></span>' for k, v in badges)
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
    rows = [{"time": e["at"][11:19], "case": e["case_id"], "event": e["type"], "node": e.get("node") or "",
             "detail": e.get("detail") or (json.dumps(e["data"]) if e.get("data") else "")} for e in events]
    return pd.DataFrame(rows, columns=["time", "case", "event", "node", "detail"])


def decision_form(case_id: str, packet: dict) -> dict | None:
    """Render the decision form; return a validated decision when submitted."""
    st.markdown("#### Your decision")
    rec = packet.get("recommendation")
    action = st.radio("Action", ACTIONS, index=ACTIONS.index(rec) if rec in ACTIONS else 0, horizontal=True,
                      key=f"action-{case_id}")
    codes = REASON_CODES[action]
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
        validate_decision(decision)
    except ValueError as e:
        st.error(str(e))
        return None
    return decision


def render_case(alert: dict, status: str | None, values: dict, packet: dict | None, events: list[dict],
                on_decision, decision_note: str | None = None) -> None:
    narrative = values.get("narrative") or {}
    evidence = {e["id"]: e for e in values.get("evidence", [])}
    case_heading(alert["case_id"], alert.get("scenario_name", ""), status)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Lane", values.get("tier", "-"))
    c2.metric("Recommendation", narrative.get("recommendation", "-"))
    c3.metric("Reason code", narrative.get("reason_code", "-"))
    c4.metric("QA rounds", values.get("qa_rounds", 0))
    c5.metric("Evidence items", len(evidence))

    review, findings_tab, evidence_tab, trace_tab, raw_tab = st.tabs(
        ["Review", "Findings", "Evidence", "Trace", "Raw state"])
    with review:
        if values.get("qa_issues"):
            st.warning("Unresolved QA issues:\n" + "\n".join(f"- [{i['severity']}] {i['description']}"
                                                             for i in values["qa_issues"]))
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
        st.divider()
        if values.get("decision"):
            d = values["decision"]
            st.success(f"Decision recorded: **{d['action']}** ({d['reason_code']}) by {d['investigator_id']}"
                       + ("" if d.get("agree_with_recommendation") else " — overrides the recommendation"))
        elif decision_note:
            st.info(decision_note)
        elif packet:
            if decision := decision_form(alert["case_id"], packet):
                on_decision(decision)
        else:
            st.error("The run ended without reaching human review.")
    with findings_tab:
        for agent in ("triage", "kyc", "txn", "screening"):
            if agent in values.get("findings", {}):
                with st.expander(agent.upper(), expanded=agent != "triage"):
                    st.json(values["findings"][agent])
    with evidence_tab:
        st.dataframe(pd.DataFrame(values.get("evidence", []), columns=["id", "agent", "source", "summary"]),
                     width="stretch", hide_index=True)
    with trace_tab:
        st.dataframe(events_frame(events), width="stretch", hide_index=True)
        st.markdown("**Prompt / model versions**")
        st.json(values.get("versions", {}))
    with raw_tab:
        st.json(values)


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
    st.dataframe(events_frame(events[::-1][:300]), width="stretch", hide_index=True)


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
    """Run (or resume) the graph, showing node-by-node progress; returns the final snapshot."""
    with st.status(label, expanded=True) as status:

        def on_update(node: str, out: dict, elapsed: float) -> None:
            lines = describe_update(node, out) or [f"[{node}]"]
            emit(case_id, "node_completed", node, "; ".join(l.split("]", 1)[-1].strip() for l in lines))
            for line in lines:
                st.write(f"`{elapsed:5.1f}s` {line}")

        try:
            snapshot = asyncio.run(stream_graph(payload, config, on_update))
        except Exception as e:  # surface model/tool errors in the UI instead of a stack trace page
            emit(case_id, "error", detail=f"{type(e).__name__}: {str(e)[:200]}")
            state.direct[case_id]["status"] = "error"
            status.update(label=f"Failed: {type(e).__name__}", state="error")
            st.exception(e)
            st.stop()
        status.update(label=f"{label}: done", state="complete", expanded=False)
    return snapshot


def direct_run(alert: dict) -> None:
    case_id = alert["case_id"]
    thread_id = f"{case_id}:ui-{uuid.uuid4().hex[:6]}"  # fresh thread so reruns don't share state
    state.setdefault("direct", {})[case_id] = {"thread_id": thread_id, "alert": alert, "status": "in_progress",
                                               "values": {}, "packet": None, "updated": now()}
    emit(case_id, "case_started", data_={"scenario": alert["scenario_code"]})
    snap = execute(case_id, initial_state(alert), run_config(thread_id), f"Investigating {case_id}")
    packet = snap.interrupts[0].value if snap.interrupts else None
    run = state.direct[case_id]
    run.update(values=snap.values, packet=packet, status="awaiting_review" if packet else "error", updated=now())
    if packet:
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
        run.update(values=snap.values, packet=None, status=STATUS_AFTER_DECISION[decision["action"]], updated=now())
        emit(case_id, "decision_applied", data_={"action": decision["action"], "reason_code": decision["reason_code"],
                                                 "investigator_id": decision["investigator_id"]})
        st.rerun()

    events = [e for e in state.get("direct_events", []) if e["case_id"] == case_id]
    render_case(run["alert"], run["status"], run["values"], run["packet"], events, on_decision)


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
    """Case state from the Postgres checkpointer (no agents run here)."""
    with sync_checkpointer() as saver:
        snap = build_graph().compile(checkpointer=saver).get_state(run_config(case_id))
    return snap.values, (snap.interrupts[0].value if snap.interrupts else None)


def publish_alert(alert: dict) -> None:
    asyncio.run(kafka.publish(ALERTS_TOPIC, [AlertEvent.model_validate(alert)]))


def publish_decision(case_id: str, decision: dict) -> None:
    asyncio.run(kafka.publish(DECISIONS_TOPIC, [DecisionEvent(case_id=case_id, **decision)]))


@st.fragment(run_every=3)
def live_progress(case_id: str, seen_status: str | None, waiting_for: str) -> None:
    """Poll status and events every 3 s; rerun the page once the worker moves the case on."""
    status = case_status(case_id)
    events = asyncio.run(kafka.read_events(case_id))
    st.markdown(f"{pill(status)} &nbsp; {waiting_for}", unsafe_allow_html=True)
    st.dataframe(events_frame(events), width="stretch", hide_index=True)
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
    note = None
    if status == "awaiting_review" and pending.get(case_id) == "decision":
        note = "Decision published to Kafka; waiting for the worker to apply it."
    if status in TERMINAL and not values.get("decision"):
        note = f"Case is {status}."

    def on_decision(decision: dict) -> None:
        publish_decision(case_id, decision)
        pending[case_id] = "decision"
        st.rerun()

    render_case(alert, status, values, packet, events, on_decision, note)
    if note and status == "awaiting_review":
        live_progress(case_id, status, "waiting for the decision to be applied")
    if status in TERMINAL:
        pending.pop(case_id, None)
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


# --- Page ---------------------------------------------------------------------------------------
def select_case() -> None:
    state.case = state.case_labels[state.case_choice]


with st.sidebar:
    mode = st.radio("Mode", [DIRECT, KAFKA], help="Kafka mode needs the Docker stack and `sentinel worker all`")
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
    else:
        if st.button("📨 Publish alert to Kafka", type="primary", width="stretch"):
            publish_alert(alert)
            state.case = alert["case_id"]
            state.setdefault("pending", {})[alert["case_id"]] = "alert"
            st.toast(f"Published {alert['case_id']} to {ALERTS_TOPIC}")
        if st.button("Open this case", width="stretch"):
            state.case = alert["case_id"]
        st.caption("The worker (`sentinel worker all`) processes alerts and decisions.")
    st.caption(f"Region `{settings.aws_region}` · models: kyc `{settings.model_kyc}`, txn `{settings.model_txn}`, "
               f"screening `{settings.model_screening}`, narrative `{settings.model_narrative}` · "
               f"Kafka `{settings.kafka_bootstrap}`")

header(mode)
if mode == DIRECT:
    direct_mode(alert, run_now)
else:
    kafka_mode()
st.markdown('<div class="sn-footer">Synthetic data only · agents recommend, investigators decide · '
            'proof of concept</div>', unsafe_allow_html=True)
