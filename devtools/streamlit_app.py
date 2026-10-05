"""Sentinel test console: a developer tool for running alerts end to end and making the human decision.

Not the investigator workbench (that is the React app, Day 5) and not deployed. It imports the graph
in-process, exactly like the CLI. Run from the repo root:

    .\\.svenv\\Scripts\\streamlit.exe run devtools/streamlit_app.py
"""

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from langgraph.types import Command

from sentinel import data
from sentinel.cli import describe_update
from sentinel.config import REPO_ROOT, settings
from sentinel.graph import compile_graph, initial_state, run_config
from sentinel.hitl import validate_decision
from sentinel.schemas import REASON_CODES

ALERT_DIR = REPO_ROOT / "data" / "fixtures" / "alerts"
ACTIONS = list(REASON_CODES)

st.set_page_config(page_title="Sentinel test console", page_icon="🛡️", layout="wide")


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
    trace = st.session_state.run["trace"]
    with st.status(label, expanded=True) as status:

        def on_update(node: str, out: dict, elapsed: float) -> None:
            lines = describe_update(node, out) or [f"[{node}]"]
            for line in lines:
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


# --- Sidebar: choose an alert --------------------------------------------------------------------
with st.sidebar:
    st.header("Alert")
    truth = data.ground_truth()
    source = st.radio("Source", ["Data backend", "Fixture file"], horizontal=True)
    if source == "Data backend":
        alerts = {a["case_id"]: a for a in data.list_alerts()}
        labels = {f"{cid} · {truth.get(cid, {}).get('typology', '?')}": cid for cid in alerts}
        alert = alerts[labels[st.selectbox("Case", list(labels))]]
    else:
        choice = st.selectbox("Fixture", [f.name for f in sorted(ALERT_DIR.glob("*.json"))])
        uploaded = st.file_uploader("…or upload an alert JSON", type="json")
        alert = json.load(uploaded) if uploaded else json.loads((ALERT_DIR / choice).read_text(encoding="utf-8"))
    with st.expander("Alert payload"):
        st.json(alert)
    if expected := truth.get(alert["case_id"]):
        with st.expander("Expected outcome (ground truth, never shown to agents)"):
            st.json(expected)
    st.caption(
        f"Data `{settings.data_backend}` · tools `{settings.tool_mode}` · OPA `{settings.opa_url or 'off'}`  \n"
        f"Region `{settings.aws_region}` · models: kyc `{settings.model_kyc}`, txn `{settings.model_txn}`, "
        f"screening `{settings.model_screening}`, narrative `{settings.model_narrative}`"
    )
    start = st.button("▶ Run investigation", type="primary", width="stretch")

st.title("Sentinel test console")
st.caption("Runs one alert through triage → KYC / Txn / Screening → Narrative → QA → human review, on live Bedrock.")

if start:
    thread_id = f"{alert['case_id']}:ui-{uuid.uuid4().hex[:6]}"  # fresh thread so reruns don't share state
    st.session_state.run = {"thread_id": thread_id, "alert": alert, "trace": []}
    snap = execute(initial_state(alert), run_config(thread_id), f"Investigating {alert['case_id']}")
    st.session_state.run["values"] = snap.values
    st.session_state.run["packet"] = snap.interrupts[0].value if snap.interrupts else None

run = st.session_state.get("run")
if not run or "values" not in run:
    st.info("Pick an alert in the sidebar and press **Run investigation**.")
    st.stop()

values, packet = run["values"], run.get("packet")
narrative = values.get("narrative") or {}
evidence = {e["id"]: e for e in values.get("evidence", [])}

# --- Headline -------------------------------------------------------------------------------------
st.subheader(f"{run['alert']['case_id']} — {run['alert']['scenario_name']}")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Lane", values.get("tier", "-"))
c2.metric("Recommendation", narrative.get("recommendation", "-"))
c3.metric("Reason code", narrative.get("reason_code", "-"))
c4.metric("QA rounds", values.get("qa_rounds", 0))
c5.metric("Evidence items", len(evidence))

review, findings_tab, evidence_tab, trace_tab, raw_tab = st.tabs(
    ["Review", "Findings", "Evidence", "Trace", "Raw state"]
)

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
    elif packet:
        st.markdown("### Your decision")
        rec = packet.get("recommendation")
        action = st.radio("Action", ACTIONS, index=ACTIONS.index(rec) if rec in ACTIONS else 0, horizontal=True)
        codes = REASON_CODES[action]
        default = codes.index(packet["reason_code"]) if packet.get("reason_code") in codes else 0
        reason_code = st.selectbox("Reason code", codes, index=default)
        investigator = st.text_input("Investigator ID", "INV-0001")
        notes = st.text_area("Notes / narrative edits (optional)")
        if st.button("Submit decision", type="primary"):
            decision = {
                "action": action,
                "reason_code": reason_code,
                "investigator_id": investigator,
                "narrative_edits": notes,
                "agree_with_recommendation": action == rec,
                "decided_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                validate_decision(decision)
            except ValueError as e:
                st.error(str(e))
                st.stop()
            snap = execute(Command(resume=decision), run_config(run["thread_id"]), "Recording decision")
            run["values"], run["packet"] = snap.values, None
            st.rerun()
    else:
        st.error("The run ended without reaching human review.")

with findings_tab:
    for agent in ("triage", "kyc", "txn", "screening"):
        if agent in values.get("findings", {}):
            with st.expander(agent.upper(), expanded=agent != "triage"):
                st.json(values["findings"][agent])

with evidence_tab:
    st.dataframe(
        pd.DataFrame(values.get("evidence", []), columns=["id", "agent", "source", "summary"]),
        width="stretch",
        hide_index=True,
    )

with trace_tab:
    st.dataframe(pd.DataFrame(run["trace"]), width="stretch", hide_index=True)
    st.markdown("**Prompt / model versions**")
    st.json(values.get("versions", {}))

with raw_tab:
    st.json(values)
