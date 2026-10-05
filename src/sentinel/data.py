"""Data access used by tools and triage. Delegates to the backend chosen by settings.data_backend:
"json" (dataset files, no services) or "postgres" (the loaded database)."""

from functools import lru_cache

from sentinel.config import settings


@lru_cache
def backend():
    if settings.data_backend == "postgres":
        from sentinel.repo.postgres_repo import PostgresRepo

        return PostgresRepo(settings.pg_dsn)
    from sentinel.repo.json_repo import JsonRepo

    return JsonRepo(settings.dataset_paths)


def get_customer(customer_id: str) -> dict | None:
    return backend().get_customer(customer_id)


def account_entity(account_id: str) -> str | None:
    return backend().account_entity(account_id)


def crm_notes_for(customer_id: str) -> list[dict]:
    return backend().crm_notes_for(customer_id)


def watchlist_entries() -> list[dict]:
    """Sanctions and PEP entries together; each carries its `list` name."""
    return backend().watchlist_entries()


def adverse_media() -> list[dict]:
    return backend().adverse_media()


def transactions_for(account_id: str, as_of: str | None = None, lookback_days: int = 90) -> list[dict]:
    """Transactions on one account, oldest first, in the window (as_of - lookback_days, as_of]."""
    return backend().transactions_for(account_id, as_of, lookback_days)


def transactions_by_ids(txn_ids: list[str]) -> list[dict]:
    return backend().transactions_by_ids(txn_ids)


def case_history_for(customer_id: str) -> list[dict]:
    return backend().case_history_for(customer_id)


def get_alert(case_id: str) -> dict | None:
    return backend().get_alert(case_id)


def list_alerts() -> list[dict]:
    return backend().list_alerts()


def ground_truth() -> dict[str, dict]:
    return backend().ground_truth()
