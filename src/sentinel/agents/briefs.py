"""Minimal per-agent briefs built from case state (TDD §6.1 `brief_for`)."""

import json

from sentinel import data
from sentinel.schemas import REASON_CODES
from sentinel.state import CaseState


def _qa_section(agent: str, state: CaseState) -> str:
    issues = [i for i in state.get("qa_issues") or [] if i["target_agent"] == agent]
    if not issues:
        return ""
    lines = "\n".join(f"- [{i['severity']}] {i['description']}" for i in issues)
    return f"\n\nQA issues with your previous output (fix all of them):\n{lines}"


def _evidence_lines(evidence: list[dict]) -> str:
    return "\n".join(f"- {e['id']}: {e['summary']}" for e in evidence)


def _alert_line(state: CaseState) -> str:
    alert = state["alert"]
    return (
        f"Case {state['case_id']}, alert {alert['alert_id']}: {alert['scenario_code']} - "
        f"{alert['scenario_name']} (score {alert['score']}), triggered {alert['triggered_at'][:10]}."
    )


def _triggering_activity(state: CaseState) -> str:
    alert = state["alert"]
    total = state.get("findings", {}).get("triage", {}).get("triggering_total")
    return (
        f"Alerted activity: {len(alert['triggering_txn_ids'])} transaction(s) "
        f"({', '.join(alert['triggering_txn_ids'])})" + (f" totalling {total:,.2f}" if total is not None else "")
    )


def brief_for(agent: str, state: CaseState) -> str:
    alert = state["alert"]

    if agent == "kyc":
        brief = (
            f"{_alert_line(state)}\nCustomer: {alert['customer_id']}\n"
            f"Accounts on the alert: {', '.join(alert['account_ids'])}\n{_triggering_activity(state)}"
        )
    elif agent == "txn":
        profile = [e for e in state.get("evidence", []) if e["id"].startswith("cust:")]
        brief = (
            f"{_alert_line(state)}\nAccounts: {', '.join(alert['account_ids'])}\n"
            f"as_of: {alert['triggered_at'][:10]}, lookback_days: {alert['lookback_days']}\n"
            f"Transactions that triggered the alert: {', '.join(alert['triggering_txn_ids'])}\n"
            f"Customer profile:\n{_evidence_lines(profile)}"
        )
    elif agent == "screening":
        # Screening is the one specialist that needs identity details (name, DOB, nationality).
        customer = data.get_customer(alert["customer_id"]) or {}
        brief = (
            f"{_alert_line(state)}\nScreen this customer ({alert['customer_id']}):\n"
            f"- name: {customer.get('name', 'unknown')}\n- date of birth: {customer.get('dob', 'unknown')}\n"
            f"- nationality: {customer.get('nationality', 'unknown')}\n"
            f"- occupation: {customer.get('occupation', 'unknown')}"
        )
    elif agent == "network":
        brief = (
            f"{_alert_line(state)} Lane: full.\nCustomer: {alert['customer_id']}\n"
            f"Accounts on the alert: {', '.join(alert['account_ids'])}"
        )
    elif agent == "typology":
        findings = _findings(state, ("triage", "kyc", "txn", "screening", "network"))
        brief = (
            f"{_alert_line(state)} Lane: {state.get('tier', 'fast')}.\n\n"
            f"Specialist findings:\n{json.dumps(findings, indent=2)}\n\n"
            f"Case evidence (cite only these IDs, plus policy IDs from your tools):\n"
            f"{_evidence_lines(state.get('evidence', []))}\n\n"
            f"Allowed reason codes:\n{json.dumps(REASON_CODES, indent=2)}"
        )
    elif agent == "narrative":
        findings = _findings(state, ("triage", "kyc", "txn", "screening", "network"))
        brief = (
            f"{_alert_line(state)} Lane: {state.get('tier', 'fast')}.\n\n"
            f"Specialist findings:\n{json.dumps(findings, indent=2)}\n\n"
            f"Typology & Policy assessment (adopt its recommendation and reason code):\n"
            f"{json.dumps(state.get('findings', {}).get('typology', {}), indent=2)}\n\n"
            f"Evidence catalogue (cite only these IDs):\n{_evidence_lines(state.get('evidence', []))}\n\n"
            f"Allowed reason codes:\n{json.dumps(REASON_CODES, indent=2)}"
        )
    elif agent == "customer_request":
        customer = data.get_customer(alert["customer_id"]) or {}
        narrative = state.get("narrative") or {}
        typology = state.get("findings", {}).get("typology") or {}
        brief = (
            f"Customer name placeholder: {customer.get('name', 'Customer')}\n"
            f"Why information is needed (internal, never quote this to the customer): "
            f"{typology.get('rationale', '')}\n"
            "Open questions from the case:\n" + "\n".join(f"- {q}" for q in narrative.get("open_questions", []))
        )
        return brief  # no tools, no rework history
    elif agent == "qa":
        findings = _findings(state, ("triage", "kyc", "txn", "screening", "network", "typology"))
        brief = (
            f"{_alert_line(state)} Lane: {state.get('tier', 'fast')}.\n\n"
            f"Findings:\n{json.dumps(findings, indent=2)}\n\n"
            f"Evidence catalogue:\n{_evidence_lines(state.get('evidence', []))}\n\n"
            f"Narrative under review:\n{json.dumps(state.get('narrative', {}), indent=2)}"
        )
        return brief  # the critic reviews the current draft; it has no tools and no rework history
    else:
        raise ValueError(f"No brief for agent {agent!r}")
    if agent in FOLLOW_UP_AGENTS:
        brief += _follow_up_section(state)
    if agent != "narrative":
        brief += f"\nlegal_entity: {state['legal_entity']} (pass it to every tool call)"
    return brief + _qa_section(agent, state)


FOLLOW_UP_AGENTS = ("kyc", "typology", "narrative", "qa")


def _follow_up_section(state: CaseState) -> str:
    """UC-04: a follow-up run after a request for information sees the reply and the previous review."""
    follow_up = state.get("follow_up")
    if not follow_up:
        return ""
    questions = "\n".join(f"- {q}" for q in follow_up.get("questions") or []) or "- (not recorded)"
    return (
        f"\n\nFOLLOW-UP ROUND {follow_up['round']} after a request for information.\n"
        f"Questions the customer was asked:\n{questions}\n"
        f"Customer reply (cite {follow_up['reply_id']}): {follow_up['reply_text']}\n"
        f"Previous review (cite {follow_up['prior_ref']}): {follow_up['prior_summary']}\n"
        "Assess whether the reply answers the questions and explains the activity, and update the "
        "assessment accordingly."
    )


def _findings(state: CaseState, agents: tuple[str, ...]) -> dict:
    return {k: v for k, v in state.get("findings", {}).items() if k in agents}
