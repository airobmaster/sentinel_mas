"""Load dataset files into Postgres (recreates the data schemas on every run)."""

from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from sentinel.repo.json_repo import merge_datasets

SCHEMA_SQL = Path(__file__).with_name("schema.sql")

TABLES = {
    "core.customers": ("customers", ["customer_id", "legal_entity", "name", "dob", "nationality", "segment",
                                      "risk_rating", "occupation", "business_purpose", "expected_monthly_credits",
                                      "expected_monthly_cash", "prior_alerts_12m"]),
    "core.accounts": ("accounts", ["account_id", "customer_id", "legal_entity", "currency"]),
    "crm.notes": ("crm_notes", ["note_id", "customer_id", "date", "text"]),
    "lake.transactions": ("transactions", ["txn_id", "account_id", "date", "direction", "amount", "currency",
                                           "channel", "branch", "counterparty", "counterparty_country",
                                           "counterparty_account", "reference"]),
    "core.device_links": ("device_links", ["customer_id", "device_id", "device_type"]),
    "screening.sanctions_list": ("sanctions_list", ["entry_id", "list", "name", "dob", "nationality", "programme"]),
    "screening.pep_list": ("pep_list", ["entry_id", "list", "name", "dob", "nationality", "position"]),
    "screening.adverse_media": ("adverse_media", ["article_id", "date", "headline", "text"]),
    "cases.history": ("case_history", ["case_id", "customer_id", "disposition", "closed_at"]),
}
# Dataset keys that are named differently in the tables
COLUMN_NAMES = {"crm.notes": {"date": "created_at"}, "lake.transactions": {"date": "txn_date"},
                "screening.adverse_media": {"date": "published_at"},
                "screening.sanctions_list": {"list": "list_name"}, "screening.pep_list": {"list": "list_name"}}


def load(paths: list[Path], dsn: str) -> dict[str, int]:
    data = merge_datasets(paths)
    counts = {}
    with psycopg.connect(dsn) as conn:
        conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        for table, (key, fields) in TABLES.items():
            columns = [COLUMN_NAMES.get(table, {}).get(f, f) for f in fields]
            with conn.cursor().copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy:
                for row in data[key]:
                    copy.write_row([row.get(f) for f in fields])
            counts[table] = len(data[key])
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO cases.alerts (case_id, legal_entity, customer_id, alert, expected) VALUES (%s, %s, %s, %s, %s)",
                [(a["case_id"], a["legal_entity"], a["customer_id"], Jsonb(a),
                  Jsonb(gt) if (gt := data["ground_truth"].get(a["case_id"])) else None)
                 for a in data["alerts"]],
            )
        counts["cases.alerts"] = len(data["alerts"])
    return counts
