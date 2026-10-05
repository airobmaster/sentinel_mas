"""Case-management tools (`case_mgmt` server). Read-only for now; draft writing comes with UC-03."""

from sentinel import data
from sentinel.tools.common import check_customer, local_tools, nothing_found, render


def get_case_history(legal_entity: str, customer_id: str) -> tuple[str, list[dict]]:
    """Prior alerts for a customer and how they were closed, oldest first."""
    check_customer(legal_entity, customer_id)
    rows = data.case_history_for(customer_id)
    if not rows:
        return nothing_found("case_history", customer_id, "case_mgmt.get_case_history",
                             f"No prior cases for {customer_id}.")
    evidence = [
        {
            "id": f"case:{h['case_id']}",
            "source": "case_mgmt.get_case_history",
            "summary": f"prior case {h['case_id']} closed {h['closed_at']} with disposition {h['disposition']}",
        }
        for h in rows
    ]
    return render(evidence), evidence


CASE_FUNCTIONS = [get_case_history]
CASE_TOOLS = local_tools(*CASE_FUNCTIONS)
