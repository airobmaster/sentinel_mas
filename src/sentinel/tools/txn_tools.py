"""Transaction tools (`txn_history` server): deterministic detectors (TDD §6.2)."""

from datetime import date

from sentinel import data
from sentinel.config import settings
from sentinel.tools.common import check_account, check_lookback, local_tools, nothing_found, render

STRUCTURING_BAND = 0.10  # deposits within 10% below the threshold
STRUCTURING_MIN_COUNT = 3
PASS_THROUGH_MIN_CREDIT = 5_000
PASS_THROUGH_MIN_SHARE = 0.80  # outflow is at least 80% of the inflow
PASS_THROUGH_MIN_PAIRS = 2


def describe(t: dict) -> str:
    text = f"{t['date']} {t['direction']} {t['amount']:,.2f} {t['currency']} {t['channel']}"
    if t.get("branch"):
        text += f" at {t['branch']} branch"
    if t.get("counterparty"):
        text += f", counterparty {t['counterparty']}"
        if t.get("counterparty_country"):
            text += f" ({t['counterparty_country']})"
    return f"{text}, ref '{t['reference']}'"


def txn_evidence(t: dict, source: str) -> dict:
    return {"id": f"txn:{t['txn_id']}", "source": source, "summary": describe(t)}


def _window(legal_entity: str, account_id: str, as_of: str, lookback_days: int) -> list[dict]:
    check_account(legal_entity, account_id)
    check_lookback(lookback_days)
    return data.transactions_for(account_id, as_of, lookback_days)


def get_transactions(legal_entity: str, account_id: str, as_of: str, lookback_days: int = 90) -> tuple[str, list[dict]]:
    """List an account's transactions in the lookback window ending on as_of (YYYY-MM-DD).
    Each line starts with the transaction's evidence ID."""
    txns = _window(legal_entity, account_id, as_of, lookback_days)
    if not txns:
        return nothing_found("transactions", account_id, "txn_history.get_transactions",
                             f"No transactions on {account_id} in the {lookback_days} days to {as_of}.")
    evidence = [txn_evidence(t, "txn_history.get_transactions") for t in txns]
    return render(evidence), evidence


def detect_structuring(legal_entity: str, account_id: str, as_of: str, lookback_days: int = 90) -> tuple[str, list[dict]]:
    """Find cash deposits just below the cash reporting threshold (possible structuring)
    in the lookback window ending on as_of (YYYY-MM-DD)."""
    threshold = settings.cash_reporting_threshold
    floor = threshold * (1 - STRUCTURING_BAND)
    hits = [
        t
        for t in _window(legal_entity, account_id, as_of, lookback_days)
        if t["channel"] == "cash" and t["direction"] == "credit" and floor <= t["amount"] < threshold
    ]
    if len(hits) < STRUCTURING_MIN_COUNT:
        return nothing_found(
            "structuring", account_id, "txn_history.detect_structuring",
            f"No structuring pattern on {account_id}: {len(hits)} cash deposit(s) between {floor:,.0f} and "
            f"{threshold:,.0f} in the {lookback_days} days to {as_of} (a pattern needs {STRUCTURING_MIN_COUNT}).",
        )
    branches = sorted({t["branch"] for t in hits if t.get("branch")})
    evidence = [txn_evidence(t, "txn_history.detect_structuring") for t in hits]
    header = (
        f"STRUCTURING PATTERN on {account_id}: {len(hits)} cash deposits between {floor:,.0f} and "
        f"{threshold:,.0f}, total {sum(t['amount'] for t in hits):,.2f}, at {len(branches)} branches "
        f"({', '.join(branches)}), from {hits[0]['date']} to {hits[-1]['date']}."
    )
    return f"{header}\n{render(evidence)}", evidence


def detect_pass_through(
    legal_entity: str, account_id: str, as_of: str, lookback_days: int = 90, max_days: int = 2
) -> tuple[str, list[dict]]:
    """Find large credits that leave the account again within max_days (funds passing straight
    through), in the lookback window ending on as_of (YYYY-MM-DD)."""
    txns = _window(legal_entity, account_id, as_of, lookback_days)
    debits = [t for t in txns if t["direction"] == "debit"]
    used: set[str] = set()
    pairs = []
    for c in (t for t in txns if t["direction"] == "credit" and t["amount"] >= PASS_THROUGH_MIN_CREDIT):
        for d in debits:
            gap = (date.fromisoformat(d["date"]) - date.fromisoformat(c["date"])).days
            if d["txn_id"] not in used and 0 <= gap <= max_days and PASS_THROUGH_MIN_SHARE * c["amount"] <= d["amount"] <= c["amount"]:
                used.add(d["txn_id"])
                pairs.append((c, d, gap))
                break
    if not pairs:
        return nothing_found(
            "pass_through", account_id, "txn_history.detect_pass_through",
            f"No pass-through pairs on {account_id} in the {lookback_days} days to {as_of} "
            f"(credit >= {PASS_THROUGH_MIN_CREDIT:,} out again within {max_days} days).",
        )
    label = "PASS-THROUGH PATTERN" if len(pairs) >= PASS_THROUGH_MIN_PAIRS else "Single matched pair (not a pattern on its own)"
    lines = [
        f"- txn:{c['txn_id']} in {c['amount']:,.2f} -> txn:{d['txn_id']} out {d['amount']:,.2f} "
        f"({d['amount'] / c['amount']:.0%}) after {gap} day(s)"
        for c, d, gap in pairs
    ]
    evidence = [txn_evidence(t, "txn_history.detect_pass_through") for c, d, _ in pairs for t in (c, d)]
    header = f"{label} on {account_id}: {len(pairs)} credit(s) moved out again within {max_days} days."
    return "\n".join([header, *lines, render(evidence)]), evidence


def velocity_stats(legal_entity: str, account_id: str, as_of: str, lookback_days: int = 90) -> tuple[str, list[dict]]:
    """Summary statistics for an account in the lookback window ending on as_of (YYYY-MM-DD):
    counts, credit/debit totals, cash ratio, pass-through ratio, velocity and distinct senders."""
    txns = _window(legal_entity, account_id, as_of, lookback_days)
    credits = [t for t in txns if t["direction"] == "credit"]
    total_in = sum(t["amount"] for t in credits)
    total_out = sum(t["amount"] for t in txns if t["direction"] == "debit")
    cash_in = sum(t["amount"] for t in credits if t["channel"] == "cash")
    cash_ratio = round(cash_in / total_in, 3) if total_in else 0.0
    passthrough_ratio = round(total_out / total_in, 3) if total_in else 0.0
    velocity = round(len(txns) / (lookback_days / 30), 2)
    senders = len({t["counterparty"] for t in credits if t.get("counterparty")})
    summary = (
        f"{lookback_days}-day stats to {as_of}: {len(txns)} transactions, credits {total_in:,.2f}, "
        f"debits {total_out:,.2f}, cash credits {cash_in:,.2f}, cash_ratio {cash_ratio}, "
        f"passthrough_ratio {passthrough_ratio}, velocity {velocity} txns per 30 days, "
        f"{senders} distinct senders"
    )
    evidence = [{"id": f"acct:{account_id}", "source": "txn_history.velocity_stats", "summary": summary}]
    return render(evidence), evidence


TXN_FUNCTIONS = [get_transactions, detect_structuring, detect_pass_through, velocity_stats]
TXN_TOOLS = local_tools(*TXN_FUNCTIONS)
