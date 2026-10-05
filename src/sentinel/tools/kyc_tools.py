"""KYC tools for the kyc agent; they move behind the `kyc_profile` MCP server later with the same names."""

from langchain_core.tools import tool

from sentinel import data
from sentinel.tools.txn_tools import render


def profile_evidence(customer: dict, source: str = "kyc_profile.get_customer_profile") -> dict:
    """Profile summary without name or DOB, so identity PII stays out of downstream briefs."""
    return {
        "id": f"cust:{customer['customer_id']}",
        "source": source,
        "summary": (
            f"{customer['segment']} customer, risk rating {customer['risk_rating']}, occupation "
            f"'{customer['occupation']}', account purpose '{customer['business_purpose']}', expected monthly "
            f"credits {customer['expected_monthly_credits']:,}, expected monthly cash "
            f"{customer['expected_monthly_cash']:,}, {customer['prior_alerts_12m']} prior alerts in 12 months, "
            f"accounts {', '.join(customer['account_ids'])}"
        ),
    }


@tool(response_format="content_and_artifact")
def get_customer_profile(customer_id: str) -> tuple[str, list[dict]]:
    """Customer profile: segment, risk rating, occupation, account purpose and expected activity."""
    customer = data.get_customer(customer_id)
    if not customer:
        return f"No customer {customer_id}.", []
    evidence = [profile_evidence(customer)]
    return render(evidence), evidence


@tool(response_format="content_and_artifact")
def get_crm_notes(customer_id: str) -> tuple[str, list[dict]]:
    """Relationship-manager and contact-centre notes for a customer, oldest first."""
    notes = data.crm_notes_for(customer_id)
    if not notes:
        return f"No CRM notes for {customer_id}.", []
    evidence = [
        {"id": f"crm:{n['note_id']}", "source": "kyc_profile.get_crm_notes", "summary": f"{n['date']}: {n['text']}"}
        for n in notes
    ]
    return render(evidence), evidence


KYC_TOOLS = [get_customer_profile, get_crm_notes]
