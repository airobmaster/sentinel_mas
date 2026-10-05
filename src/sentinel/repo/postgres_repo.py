"""Repository over the Postgres schemas created by `sentinel data load` (TDD §3.3)."""

from datetime import date, timedelta
from decimal import Decimal

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def clean(row: dict) -> dict:
    """Match the JSON backend: ISO date strings and float amounts."""
    return {
        k: v.isoformat() if isinstance(v, date) else float(v) if isinstance(v, Decimal) else v
        for k, v in row.items()
    }


TXN_COLUMNS = (
    "txn_id, account_id, txn_date AS date, direction, amount, currency, channel, branch, "
    "counterparty, counterparty_country, reference"
)


class PostgresRepo:
    def __init__(self, dsn: str):
        self.pool = ConnectionPool(dsn, min_size=1, max_size=8, kwargs={"row_factory": dict_row}, open=True)

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.pool.connection() as conn:
            return [clean(r) for r in conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self._all(sql, params)
        return rows[0] if rows else None

    def get_customer(self, customer_id: str) -> dict | None:
        return self._one(
            """SELECT c.*, COALESCE(array_agg(a.account_id ORDER BY a.account_id)
                         FILTER (WHERE a.account_id IS NOT NULL), '{}') AS account_ids
               FROM core.customers c LEFT JOIN core.accounts a USING (customer_id)
               WHERE c.customer_id = %s GROUP BY c.customer_id""",
            (customer_id,),
        )

    def account_entity(self, account_id: str) -> str | None:
        row = self._one("SELECT legal_entity FROM core.accounts WHERE account_id = %s", (account_id,))
        return row["legal_entity"] if row else None

    def crm_notes_for(self, customer_id: str) -> list[dict]:
        return self._all(
            "SELECT note_id, customer_id, created_at AS date, text FROM crm.notes "
            "WHERE customer_id = %s ORDER BY created_at, note_id",
            (customer_id,),
        )

    def watchlist_entries(self) -> list[dict]:
        sanctions = self._all(
            'SELECT list_name AS "list", entry_id, name, dob, nationality, programme '
            "FROM screening.sanctions_list ORDER BY entry_id"
        )
        peps = self._all(
            'SELECT list_name AS "list", entry_id, name, dob, nationality, position '
            "FROM screening.pep_list ORDER BY entry_id"
        )
        return sanctions + peps

    def adverse_media(self) -> list[dict]:
        return self._all(
            "SELECT article_id, published_at AS date, headline, text FROM screening.adverse_media ORDER BY article_id"
        )

    def transactions_for(self, account_id: str, as_of: str | None = None, lookback_days: int = 90) -> list[dict]:
        if not as_of:
            return self._all(
                f"SELECT {TXN_COLUMNS} FROM lake.transactions WHERE account_id = %s ORDER BY txn_date, txn_id",
                (account_id,),
            )
        end = date.fromisoformat(as_of[:10])
        return self._all(
            f"SELECT {TXN_COLUMNS} FROM lake.transactions "
            "WHERE account_id = %s AND txn_date > %s AND txn_date <= %s ORDER BY txn_date, txn_id",
            (account_id, end - timedelta(days=lookback_days), end),
        )

    def transactions_by_ids(self, txn_ids: list[str]) -> list[dict]:
        return self._all(
            f"SELECT {TXN_COLUMNS} FROM lake.transactions WHERE txn_id = ANY(%s) ORDER BY txn_id", (list(txn_ids),)
        )

    def case_history_for(self, customer_id: str) -> list[dict]:
        return self._all(
            "SELECT case_id, customer_id, disposition, closed_at FROM cases.history "
            "WHERE customer_id = %s ORDER BY closed_at, case_id",
            (customer_id,),
        )

    def get_alert(self, case_id: str) -> dict | None:
        row = self._one("SELECT alert FROM cases.alerts WHERE case_id = %s", (case_id,))
        return row["alert"] if row else None

    def list_alerts(self) -> list[dict]:
        return [r["alert"] for r in self._all("SELECT alert FROM cases.alerts ORDER BY case_id")]

    def ground_truth(self) -> dict[str, dict]:
        rows = self._all("SELECT case_id, expected FROM cases.alerts WHERE expected IS NOT NULL")
        return {r["case_id"]: r["expected"] for r in rows}
