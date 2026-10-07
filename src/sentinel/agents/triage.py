"""Triage: deterministic lane rules (BR-02). The LLM upgrade step (FR-012) comes in a later slice."""

from sentinel import data
from sentinel.config import settings
from sentinel.state import CaseState
from sentinel.tools.kyc_tools import profile_evidence

NETWORK_SCENARIO_MARKERS = ("MULE", "FANIN", "FAN-IN", "FANOUT", "FAN-OUT", "NETWORK")


def lane_rules(alert: dict, customer: dict | None, triggering_total: float) -> list[str]:
    """Return the BR-02 rules that force the full lane; empty means fast lane."""
    hits = []
    if customer and customer.get("risk_rating") == "high":
        hits.append("customer risk rating is high")
    if alert.get("sanctions_indicator") or alert.get("pep_indicator"):
        hits.append("sanctions/PEP indication on the alert")
    if triggering_total >= settings.full_lane_amount:
        hits.append(f"alert amount {triggering_total:,.2f} >= {settings.full_lane_amount:,.0f}")
    if customer and customer.get("prior_alerts_12m", 0) >= 2:
        hits.append(f"{customer['prior_alerts_12m']} prior alerts in 12 months")
    if any(m in alert["scenario_code"].upper() for m in NETWORK_SCENARIO_MARKERS):
        hits.append("network-type scenario")
    if len(set(alert.get("customer_ids", [alert["customer_id"]]))) > 1:
        hits.append("multiple customers on the alert")
    return hits


async def triage(state: CaseState) -> dict:
    alert = state["alert"]
    customer = data.get_customer(alert["customer_id"])
    triggering_total = sum(t["amount"] for t in data.transactions_by_ids(alert["triggering_txn_ids"]))
    hits = lane_rules(alert, customer, triggering_total)
    tier = "full" if hits else "fast"
    alert_evidence = {
        "id": f"alert:{alert['case_id']}", "source": "case_mgmt.get_alert", "agent": "triage",
        "summary": (f"alert {alert['alert_id']} ({alert['scenario_code']}, score {alert['score']}): "
                    f"{len(alert['triggering_txn_ids'])} triggering transaction(s) totalling {triggering_total:,.2f}; "
                    f"lane {tier}" + (f" because {'; '.join(hits)}" if hits else "")),
    }
    profile = [{**profile_evidence(customer, "case_mgmt.get_customer"), "agent": "triage"}] if customer else []
    return {
        "tier": tier,
        "findings": {
            "triage": {
                "scenario": alert["scenario_code"],
                "tier": tier,
                "rule_hits": hits,
                "triggering_total": triggering_total,
            }
        },
        "evidence": [alert_evidence, *profile],
    }
