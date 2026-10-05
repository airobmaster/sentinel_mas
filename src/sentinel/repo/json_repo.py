"""Repository over one or more JSON dataset files (fixtures and/or a generated dataset)."""

import json
from datetime import date, timedelta
from pathlib import Path

LIST_KEYS = (
    "customers", "accounts", "crm_notes", "sanctions_list", "pep_list",
    "adverse_media", "transactions", "alerts", "case_history",
)


def account_rows(data: dict) -> list[dict]:
    """Accounts from the dataset, or derived from customers' account_ids (fixtures have no accounts list)."""
    if data.get("accounts"):
        return data["accounts"]
    currency = {"UK": "GBP", "ES": "EUR"}
    return [
        {"account_id": a, "customer_id": c["customer_id"], "legal_entity": c["legal_entity"],
         "currency": currency.get(c["legal_entity"], "GBP")}
        for c in data.get("customers", [])
        for a in c["account_ids"]
    ]


def merge_datasets(paths: list[Path]) -> dict:
    data: dict = {k: [] for k in LIST_KEYS} | {"ground_truth": {}}
    for path in paths:
        if not Path(path).exists():
            continue
        part = json.loads(Path(path).read_text(encoding="utf-8"))
        part["accounts"] = account_rows(part)
        for key in LIST_KEYS:
            data[key].extend(part.get(key, []))
        data["ground_truth"].update(part.get("ground_truth", {}))
    for t in data["transactions"]:
        t.setdefault("counterparty_country", None)
    return data


class JsonRepo:
    def __init__(self, paths: list[Path]):
        self.data = merge_datasets(paths)
        self._customers = {c["customer_id"]: c for c in self.data["customers"]}
        self._accounts = {a["account_id"]: a for a in self.data["accounts"]}
        self._alerts = {a["case_id"]: a for a in self.data["alerts"]}

    def get_customer(self, customer_id: str) -> dict | None:
        return self._customers.get(customer_id)

    def account_entity(self, account_id: str) -> str | None:
        account = self._accounts.get(account_id)
        return account["legal_entity"] if account else None

    def crm_notes_for(self, customer_id: str) -> list[dict]:
        notes = [n for n in self.data["crm_notes"] if n["customer_id"] == customer_id]
        return sorted(notes, key=lambda n: (n["date"], n["note_id"]))

    def watchlist_entries(self) -> list[dict]:
        return self.data["sanctions_list"] + self.data["pep_list"]

    def adverse_media(self) -> list[dict]:
        return self.data["adverse_media"]

    def transactions_for(self, account_id: str, as_of: str | None = None, lookback_days: int = 90) -> list[dict]:
        txns = [t for t in self.data["transactions"] if t["account_id"] == account_id]
        if as_of:
            end = date.fromisoformat(as_of[:10])
            start = end - timedelta(days=lookback_days)
            txns = [t for t in txns if start < date.fromisoformat(t["date"]) <= end]
        return sorted(txns, key=lambda t: (t["date"], t["txn_id"]))

    def transactions_by_ids(self, txn_ids: list[str]) -> list[dict]:
        wanted = set(txn_ids)
        return sorted((t for t in self.data["transactions"] if t["txn_id"] in wanted), key=lambda t: t["txn_id"])

    def case_history_for(self, customer_id: str) -> list[dict]:
        rows = [h for h in self.data["case_history"] if h["customer_id"] == customer_id]
        return sorted(rows, key=lambda h: (h["closed_at"], h["case_id"]))

    def get_alert(self, case_id: str) -> dict | None:
        return self._alerts.get(case_id)

    def list_alerts(self) -> list[dict]:
        return sorted(self._alerts.values(), key=lambda a: a["case_id"])

    def ground_truth(self) -> dict[str, dict]:
        return self.data["ground_truth"]
