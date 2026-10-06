"""Sentinel test console: a developer tool for running alerts end to end and making the human decision.

Two modes:
- Direct: runs the graph in this process with an in-memory checkpointer. No Docker needed.
- Kafka: publishes the alert to Kafka; a `sentinel worker` process investigates it; the review
  state is read from the Postgres checkpointer; your decision is published to Kafka.
Not the investigator workbench (the React app) and not deployed. Run from the repo root:

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
from sentinel.persistence import OPEN_STATUSES, case_status, list_cases, reset_case, sync_checkpointer
from sentinel.schemas import REASON_CODES

if sys.platform == "win32":  # psycopg's async driver cannot use the default Proactor loop on Windows
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

ALERT_DIR = REPO_ROOT / "data" / "fixtures" / "alerts"
ACTIONS = list(REASON_CODES)
STATUS_ICON = {"new": "⚪", "in_progress": "🔵", "awaiting_review": "🟡", "closed": "🟢", "escalated": "🔴",
               "info_requested": "🟣", "error": "⛔", None: "⚪"}
TERMINAL = ("closed", "escalated", "info_requested")
DIRECT, KAFKA = "Direct (in-process)", "Kafka (full stack)"

st.set_page_config(page_title="Sentinel test console", page_icon="🛡️", layout="wide")
state = st.session_state


# --- Shared rendering ---------------------------------------------------------------------------
def decision_form(case_id: str, packet: dict) -> dict | None:
    """Render the decision form; return a validated decision when submitted."""
    st.markdown("### Your decision")
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
                "decided_at": datetime.now(timezone.utc).isoformat()}
    try:
        validate_decision(decision)
    except ValueError as e:
        st.error(str(e))
        return None
    return decision


def render_case(alert: dict, values: dict, packet: dict | None, trace: pd.DataFrame, on_decision,
                decision_note: str | None = None) -> None:
    narrative = values.get("narrative") or {}
    evidence = {e["id"]: e for e in values.get("evidence", [])}
    st.subheader(f"{alert['case_id']} — {alert['scenario_name']}")
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
        st.dataframe(trace, width="stretch", hide_index=True)
        st.markdown("**Prompt / model versions**")
        st.json(values.get("versions", {}))
    with raw_tab:
        st.json(values)


# --- Direct mode --------------------------------------------------------------------------------
@st.cache_resource
def get_graph():
    """One compiled graph + in-memory checkpointer for the life of the Streamlit server."""
    return compile_graph()


async def stream_graph(payload, config: dict, on_update) -> object:
    start = time.perf_counter()
    async for update in get_graph().astream(payload, config, stream_mode="updates"):
        for node, out in update.items():
            if node != "__interrupt__":
                on_update(node, out or {}, time.perf_counter() - start)
    return await get_graph().aget_state(config)


def execute(payload, config: dict, label: str):
    """Run (or resume) the graph, showing node-by-node progress; returns the final snapshot."""
    trace = state.run["trace"]
    with st.status(label, expanded=True) as status:

        def on_update(node: str, out: dict, elapsed: float) -> None:
            for line in describe_update(node, out) or [f"[{node}]"]:
                trace.append({"t (s)": round(elapsed, 1), "node": node, "detail": line.split("]", 1)[-1].strip()})
                st.write(f"`{elapsed:5.1f}s` {line}")

        try:
            snapshot = asyncio.run(stream_graph(payload, config, on_update))
        except Exception as e:  # surface model/tool errors in the UI instead of a stack trace page
            status.update(label=f"Failed: {type(e).__name__}", state="error")
            st.exception(e)
            st.stop()
        status.update(label=f"{label}: done", state="complete", expanded=False)
    return snapshot


def direct_mode(alert: dict, start: bool) -> None:
    st.caption("Runs one alert in this process: triage → KYC / Txn / Screening → Narrative → QA → human review.")
    if start:
        thread_id = f"{alert['case_id']}:ui-{uuid.uuid4().hex[:6]}"  # fresh thread so reruns don't share state
        state.run = {"thread_id": thread_id, "alert": alert, "trace": []}
        snap = execute(initial_state(alert), run_config(thread_id), f"Investigating {alert['case_id']}")
        state.run["values"] = snap.values
        state.run["packet"] = snap.interrupts[0].value if snap.interrupts else None
    run = state.get("run")
    if not run or "values" not in run:
        st.info("Pick an alert in the sidebar and press **Run investigation**.")
        return

    def on_decision(decision: dict) -> None:
        snap = execute(Command(resume=decision), run_config(run["thread_id"]), "Recording decision")
        run["values"], run["packet"] = snap.values, None
        st.rerun()

    render_case(run["alert"], run["values"], run.get("packet"), pd.DataFrame(run["trace"]), on_decision)


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


def events_frame(events: list[dict]) -> pd.DataFrame:
    rows = [{"time": e["at"][11:19], "case": e["case_id"], "event": e["type"], "node": e.get("node") or "",
             "detail": e.get("detail") or (json.dumps(e["data"]) if e.get("data") else "")} for e in events]
    return pd.DataFrame(rows, columns=["time", "case", "event", "node", "detail"])


def publish_alert(alert: dict) -> None:
    asyncio.run(kafka.publish(ALERTS_TOPIC, [AlertEvent.model_validate(alert)]))


def publish_decision(case_id: str, decision: dict) -> None:
    asyncio.run(kafka.publish(DECISIONS_TOPIC, [DecisionEvent(case_id=case_id, **decision)]))


@st.fragment(run_every=3)
def live_progress(case_id: str, seen_status: str | None, waiting_for: str) -> None:
    """Poll status and events every 3 s; rerun the page once the worker moves the case on."""
    status = case_status(case_id)
    events = asyncio.run(kafka.read_events(case_id))
    st.markdown(f"{STATUS_ICON.get(status, '⚪')} **{status or 'not started'}** — {waiting_for}")
    st.dataframe(events_frame(events), width="stretch", hide_index=True)
    if status != seen_status:
        st.rerun(scope="app")


def kafka_case_view(case_id: str) -> None:
    alert = data.get_alert(case_id) or {"case_id": case_id, "scenario_name": ""}
    status = case_status(case_id)
    pending = state.setdefault("pending", {})

    head, b1, b2 = st.columns([4, 1.3, 1])
    head.markdown(f"#### {STATUS_ICON.get(status, '⚪')} {case_id} · `{status or 'not started'}`")
    if status and status != "new" and b1.button("Re-publish alert", help="Should be ignored as a duplicate"):
        publish_alert(alert)
        st.toast("Alert re-published; the worker should report duplicate_ignored")
    if status and b2.button("Reset case", help="Dev only: delete the checkpoint so the alert can run again"):
        reset_case(case_id)
        pending.pop(case_id, None)
        st.rerun()

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
    trace = events_frame(asyncio.run(kafka.read_events(case_id)))
    note = None
    if status == "awaiting_review" and pending.get(case_id) == "decision":
        note = "Decision published to Kafka; waiting for the worker to apply it."
    if status in TERMINAL and not values.get("decision"):
        note = f"Case is {status}."

    def on_decision(decision: dict) -> None:
        publish_decision(case_id, decision)
        pending[case_id] = "decision"
        st.rerun()

    render_case(alert, values, packet, trace, on_decision, note)
    if note and status == "awaiting_review":
        live_progress(case_id, status, "waiting for the decision to be applied")
    if status in TERMINAL:
        pending.pop(case_id, None)
        if st.button("Send another decision", help="Should be ignored: the case is no longer waiting for review"):
            publish_decision(case_id, {"action": "close", "reason_code": "FP_DATA_ERROR",
                                       "investigator_id": "INV-0001"})
            st.toast("Decision sent; the worker should report decision_ignored")


def kafka_mode() -> None:
    st.caption("Publishes alerts to Kafka; `sentinel worker` investigates them; state is read from Postgres.")
    missing = [name for name, hp in (("Kafka", settings.kafka_bootstrap), ("Postgres", pg_host_port()))
               if not reachable(hp)]
    if missing:
        st.error(f"{' and '.join(missing)} not reachable. Start the stack with `docker compose up -d --build`.")
        st.stop()
    case_tab, queue_tab, stream_tab = st.tabs(["Case", "Work queue", "Event stream"])
    with queue_tab:
        statuses = st.multiselect("Status", list(OPEN_STATUSES) + list(TERMINAL) + ["error"],
                                  default=list(OPEN_STATUSES) + list(TERMINAL) + ["error"])
        rows = [r for r in list_cases(statuses) if r["status"] != "new"] if statuses else []
        queue = pd.DataFrame([{"status": f"{STATUS_ICON.get(r['status'], '')} {r['status']}", "case": r["case_id"],
                               "scenario": r["scenario"], "typology": r["typology"], "entity": r["legal_entity"],
                               "updated": r["updated_at"].strftime("%H:%M:%S")} for r in rows],
                             columns=["status", "case", "scenario", "typology", "entity", "updated"])
        st.caption(f"{len(queue)} case(s) the workers have touched. Select a row to open it in the Case tab.")
        picked = st.dataframe(queue, width="stretch", hide_index=True, on_select="rerun",
                              selection_mode="single-row", key="queue")
        if picked.selection.rows:
            state.kafka_case = queue.iloc[picked.selection.rows[0]]["case"]
    with stream_tab:
        if st.button("Refresh events"):
            pass
        events = asyncio.run(kafka.read_events())
        st.caption(f"{len(events)} event(s) on aml.case-events.v1 (newest first)")
        st.dataframe(events_frame(events[::-1][:300]), width="stretch", hide_index=True)
    with case_tab:
        kafka_case_view(state.kafka_case)


# --- Page ---------------------------------------------------------------------------------------
def select_case() -> None:
    state.kafka_case = state.case_labels[state.case_choice]


with st.sidebar:
    mode = st.radio("Mode", [DIRECT, KAFKA], help="Kafka mode needs the Docker stack and `sentinel worker all`")
    st.header("Alert")
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
    state.setdefault("kafka_case", alert["case_id"])
    with st.expander("Alert payload"):
        st.json(alert)
    if expected := truth.get(alert["case_id"]):
        with st.expander("Expected outcome (ground truth, never shown to agents)"):
            st.json(expected)
    if mode == DIRECT:
        start = st.button("▶ Run investigation", type="primary", width="stretch")
    else:
        if st.button("📨 Publish alert to Kafka", type="primary", width="stretch"):
            publish_alert(alert)
            state.kafka_case = alert["case_id"]
            state.setdefault("pending", {})[alert["case_id"]] = "alert"
            st.toast(f"Published {alert['case_id']} to {ALERTS_TOPIC}")
        if st.button("Open this case", width="stretch"):
            state.kafka_case = alert["case_id"]
        st.caption("Run `sentinel worker all` in a terminal to process alerts and decisions.")
    st.caption(
        f"Data `{settings.data_backend}` · tools `{settings.tool_mode}` · OPA `{settings.opa_url or 'off'}` · "
        f"Kafka `{settings.kafka_bootstrap}`  \nRegion `{settings.aws_region}` · models: kyc `{settings.model_kyc}`, "
        f"txn `{settings.model_txn}`, screening `{settings.model_screening}`, narrative `{settings.model_narrative}`"
    )

st.title("Sentinel test console")
if mode == DIRECT:
    direct_mode(alert, start)
else:
    kafka_mode()
