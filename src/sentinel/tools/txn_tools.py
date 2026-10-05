"""Transaction tools for the txn agent (TDD §6.2 deterministic detectors).

Slice 1 runs them in-process; they move behind the `txn_history` MCP server later with the same names.
Each tool returns (content for the model, evidence list as the artifact). The graph collects evidence
from the artifacts, so an agent can only cite what a tool actually returned.
"""

from langchain_core.tools import tool

from sentinel import data
from sentinel.config import settings

STRUCTURING_BAND = 0.10  # deposits within 10% below the threshold
STRUCTURING_MIN_COUNT = 3


def describe(t: dict) -> str:
    text = f"{t['date']} {t['direction']} {t['amount']:,.2f} {t['currency']} {t['channel']}"
    if t.get("branch"):
        text += f" at {t['branch']} branch"
    if t.get("counterparty"):
        text += f", counterparty {t['counterparty']}"
    return f"{text}, ref '{t['reference']}'"


def txn_evidence(t: dict, source: str) -> dict:
    return {"id": f"txn:{t['txn_id']}", "source": source, "summary": describe(t)}


def render(evidence: list[dict]) -> str:
    return "\n".join(f"{e['id']}: {e['summary']}" for e in evidence)


@tool(response_format="content_and_artifact")
def get_transactions(account_id: str, as_of: str, lookback_days: int = 90) -> tuple[str, list[dict]]:
    """List an account's transactions in the lookback window ending on as_of (YYYY-MM-DD).
    Each line starts with the transaction's evidence ID."""
    txns = data.transactions_for(account_id, as_of, lookback_days)
    if not txns:
        return f"No transactions on {account_id} in the {lookback_days} days to {as_of}.", []
    evidence = [txn_evidence(t, "txn_history.get_transactions") for t in txns]
    return render(evidence), evidence


@tool(response_format="content_and_artifact")
def detect_structuring(account_id: str, as_of: str, lookback_days: int = 90) -> tuple[str, list[dict]]:
    """Find cash deposits just below the cash reporting threshold (possible structuring)
    in the lookback window ending on as_of (YYYY-MM-DD)."""
    threshold = settings.cash_reporting_threshold
    floor = threshold * (1 - STRUCTURING_BAND)
    hits = [
        t
        for t in data.transactions_for(account_id, as_of, lookback_days)
        if t["channel"] == "cash" and t["direction"] == "credit" and floor <= t["amount"] < threshold
    ]
    if len(hits) < STRUCTURING_MIN_COUNT:
        return (
            f"No structuring pattern on {account_id}: {len(hits)} cash deposit(s) between "
            f"{floor:,.0f} and {threshold:,.0f} (minimum for a pattern is {STRUCTURING_MIN_COUNT}).",
            [],
        )
    branches = sorted({t["branch"] for t in hits if t.get("branch")})
    evidence = [txn_evidence(t, "txn_history.detect_structuring") for t in hits]
    header = (
        f"STRUCTURING PATTERN on {account_id}: {len(hits)} cash deposits between {floor:,.0f} and "
        f"{threshold:,.0f}, total {sum(t['amount'] for t in hits):,.2f}, at {len(branches)} branches "
        f"({', '.join(branches)}), from {hits[0]['date']} to {hits[-1]['date']}."
    )
    return f"{header}\n{render(evidence)}", evidence


@tool(response_format="content_and_artifact")
def velocity_stats(account_id: str, as_of: str, lookback_days: int = 90) -> tuple[str, list[dict]]:
    """Summary statistics for an account in the lookback window ending on as_of (YYYY-MM-DD):
    counts, credit/debit totals, cash ratio, pass-through ratio and velocity."""
    txns = data.transactions_for(account_id, as_of, lookback_days)
    credits = [t for t in txns if t["direction"] == "credit"]
    total_in = sum(t["amount"] for t in credits)
    total_out = sum(t["amount"] for t in txns if t["direction"] == "debit")
    cash_in = sum(t["amount"] for t in credits if t["channel"] == "cash")
    cash_ratio = round(cash_in / total_in, 3) if total_in else 0.0
    passthrough_ratio = round(total_out / total_in, 3) if total_in else 0.0
    velocity = round(len(txns) / (lookback_days / 30), 2)
    summary = (
        f"{lookback_days}-day stats to {as_of}: {len(txns)} transactions, credits {total_in:,.2f}, "
        f"debits {total_out:,.2f}, cash credits {cash_in:,.2f}, cash_ratio {cash_ratio}, "
        f"passthrough_ratio {passthrough_ratio}, velocity {velocity} txns per 30 days"
    )
    evidence = [{"id": f"acct:{account_id}", "source": "txn_history.velocity_stats", "summary": summary}]
    return render(evidence), evidence


TXN_TOOLS = [get_transactions, detect_structuring, velocity_stats]
