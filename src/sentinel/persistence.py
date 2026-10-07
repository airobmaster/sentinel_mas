"""Durable state for workers: the LangGraph Postgres checkpointer (TDD §5.3) and case status
in `cases.alerts` (the case-management record the API's work queue reads).

Async helpers are used by the workers; the small sync helpers at the bottom are for tools
that only read state or reset it (the Streamlit console, scripts)."""

from contextlib import asynccontextmanager, contextmanager

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from sentinel.config import settings

CHECKPOINT_SCHEMA = "sentinel"  # checkpoint tables live apart from the reloadable data schemas
CONNECTION_KWARGS = {"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row,
                     "options": f"-c search_path={CHECKPOINT_SCHEMA},public"}

# Case status values in cases.alerts.status
STATUS_AFTER_DECISION = {"close": "closed", "escalate": "escalated", "request_info": "info_requested"}
OPEN_STATUSES = ("new", "in_progress", "awaiting_review")


class CaseStore:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool

    async def start(self, alert: dict) -> None:
        """Record a case as in progress (inserting it if the alert arrived only via Kafka)."""
        async with self.pool.connection() as conn:
            await conn.execute(
                """INSERT INTO cases.alerts (case_id, legal_entity, customer_id, alert, status)
                   VALUES (%s, %s, %s, %s, 'in_progress')
                   ON CONFLICT (case_id) DO UPDATE SET status = 'in_progress', updated_at = now()""",
                (alert["case_id"], alert["legal_entity"], alert["customer_id"], Jsonb(alert)),
            )

    async def set_status(self, case_id: str, status: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("UPDATE cases.alerts SET status = %s, updated_at = now() WHERE case_id = %s",
                               (status, case_id))

    async def status(self, case_id: str) -> str | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute("SELECT status FROM cases.alerts WHERE case_id = %s", (case_id,))).fetchone()
        return row["status"] if row else None


@asynccontextmanager
async def durable_state():
    """Yield (checkpointer, case store) backed by one Postgres pool."""
    async with AsyncConnectionPool(settings.pg_dsn, max_size=10, open=False, kwargs=CONNECTION_KWARGS) as pool:
        async with pool.connection() as conn:
            await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {CHECKPOINT_SCHEMA}")
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        yield checkpointer, CaseStore(pool)


# --- Sync helpers (console, scripts) ------------------------------------------------------------
@contextmanager
def sync_checkpointer():
    """A read-mostly sync checkpointer on its own connection (tables created if no worker has run yet)."""
    with psycopg.connect(settings.pg_dsn, **CONNECTION_KWARGS) as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {CHECKPOINT_SCHEMA}")
        saver = PostgresSaver(conn)
        saver.setup()
        yield saver


def list_cases(statuses: list[str] | None = None) -> list[dict]:
    """The work queue: cases with status, most recently updated first."""
    sql = ("SELECT case_id, status, legal_entity, alert->>'scenario_name' AS scenario, "
           "expected->>'typology' AS typology, updated_at FROM cases.alerts")
    params: tuple = ()
    if statuses:
        sql += " WHERE status = ANY(%s)"
        params = (statuses,)
    with psycopg.connect(settings.pg_dsn, **CONNECTION_KWARGS) as conn:
        return conn.execute(sql + " ORDER BY updated_at DESC, case_id", params).fetchall()


def case_status(case_id: str) -> str | None:
    with psycopg.connect(settings.pg_dsn, **CONNECTION_KWARGS) as conn:
        row = conn.execute("SELECT status FROM cases.alerts WHERE case_id = %s", (case_id,)).fetchone()
    return row["status"] if row else None


def reset_case(case_id: str) -> None:
    """Dev/test only: forget a case's run so the same alert can be investigated again."""
    with sync_checkpointer() as saver:
        saver.delete_thread(case_id)
        saver.conn.execute("UPDATE cases.alerts SET status = 'new', updated_at = now() WHERE case_id = %s",
                           (case_id,))
