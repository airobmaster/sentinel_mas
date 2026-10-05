"""Read access to the JSON fixtures. A later slice replaces this with Postgres behind MCP."""

import json
from datetime import date, timedelta
from functools import lru_cache

from sentinel.config import settings


@lru_cache
def load_fixtures() -> dict:
    return json.loads(settings.fixtures_path.read_text(encoding="utf-8"))


def get_customer(customer_id: str) -> dict | None:
    return next((c for c in load_fixtures()["customers"] if c["customer_id"] == customer_id), None)


def crm_notes_for(customer_id: str) -> list[dict]:
    return sorted(
        (n for n in load_fixtures()["crm_notes"] if n["customer_id"] == customer_id), key=lambda n: n["date"]
    )


def watchlist_entries() -> list[dict]:
    """Sanctions and PEP entries together; each carries its `list` name."""
    return load_fixtures()["sanctions_list"] + load_fixtures()["pep_list"]


def adverse_media() -> list[dict]:
    return load_fixtures()["adverse_media"]


def transactions_for(account_id: str, as_of: str | None = None, lookback_days: int = 90) -> list[dict]:
    """Transactions on one account, oldest first, in the window (as_of - lookback_days, as_of]."""
    txns = [t for t in load_fixtures()["transactions"] if t["account_id"] == account_id]
    if as_of:
        end = date.fromisoformat(as_of[:10])
        start = end - timedelta(days=lookback_days)
        txns = [t for t in txns if start < date.fromisoformat(t["date"]) <= end]
    return sorted(txns, key=lambda t: t["date"])


def transactions_by_ids(txn_ids: list[str]) -> list[dict]:
    wanted = set(txn_ids)
    return [t for t in load_fixtures()["transactions"] if t["txn_id"] in wanted]
