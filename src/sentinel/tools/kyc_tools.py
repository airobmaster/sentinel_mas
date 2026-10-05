"""KYC tools (`kyc_profile` server)."""

from sentinel import data
from sentinel.tools.common import check_customer, local_tools, nothing_found, render


def profile_evidence(customer: dict, source: str = "kyc_profile.get_customer_profile") -> dict:
    """Profile summary without name or DOB, so identity PII stays out of downstream briefs."""
    return {
        "id": f"cust:{customer['customer_id']}",
        "source": source,
        "summary": (
            f"{customer['segment']} customer, risk rating {customer['risk_rating']}, occupation "
            f"'{customer['occupation']}', account purpose '{customer['business_purpose']}', expected monthly "
            f"credits {customer['expected_monthly_credits']:,.0f}, expected monthly cash "
            f"{customer['expected_monthly_cash']:,.0f}, {customer['prior_alerts_12m']} prior alerts in 12 months, "
            f"accounts {', '.join(customer['account_ids'])}"
        ),
    }


def get_customer_profile(legal_entity: str, customer_id: str) -> tuple[str, list[dict]]:
    """Customer profile: segment, risk rating, occupation, account purpose and expected activity."""
    evidence = [profile_evidence(check_customer(legal_entity, customer_id))]
    return render(evidence), evidence


def get_crm_notes(legal_entity: str, customer_id: str) -> tuple[str, list[dict]]:
    """Relationship-manager and contact-centre notes for a customer, oldest first."""
    check_customer(legal_entity, customer_id)
    notes = data.crm_notes_for(customer_id)
    if not notes:
        return nothing_found("crm_notes", customer_id, "kyc_profile.get_crm_notes", f"No CRM notes for {customer_id}.")
    evidence = [
        {"id": f"crm:{n['note_id']}", "source": "kyc_profile.get_crm_notes", "summary": f"{n['date']}: {n['text']}"}
        for n in notes
    ]
    return render(evidence), evidence


KYC_FUNCTIONS = [get_customer_profile, get_crm_notes]
KYC_TOOLS = local_tools(*KYC_FUNCTIONS)
