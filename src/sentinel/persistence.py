"""Durable state for workers: the LangGraph Postgres checkpointer (TDD §5.3) and case status
in `cases.alerts` (the case-management record the API's work queue reads)."""

from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from sentinel.config import settings

CHECKPOINT_SCHEMA = "sentinel"  # checkpoint tables live apart from the reloadable data schemas

# Case status values in cases.alerts.status
STATUS_AFTER_DECISION = {"close": "closed", "escalate": "escalated", "request_info": "info_requested"}


class CaseStore:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool

    async def start(self, alert: dict) -> None:
        """Record a case as in progress (inserting it if the alert arrived only via Kafka)."""
        async with self.pool.connection() as conn:
            await conn.execute(
                """INSERT INTO cases.alerts (case_id, legal_entity, customer_id, alert, status)
                   VALUES (%s, %s, %s, %s, 'in_progress')
                   ON CONFLICT (case_id) DO UPDATE SET status = 'in_progress'""",
                (alert["case_id"], alert["legal_entity"], alert["customer_id"], Jsonb(alert)),
            )

    async def set_status(self, case_id: str, status: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("UPDATE cases.alerts SET status = %s WHERE case_id = %s", (status, case_id))

    async def status(self, case_id: str) -> str | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute("SELECT status FROM cases.alerts WHERE case_id = %s", (case_id,))).fetchone()
        return row["status"] if row else None


@asynccontextmanager
async def durable_state():
    """Yield (checkpointer, case store) backed by one Postgres pool."""
    async with AsyncConnectionPool(
        settings.pg_dsn,
        max_size=10,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row,
                "options": f"-c search_path={CHECKPOINT_SCHEMA},public"},
    ) as pool:
        async with pool.connection() as conn:
            await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {CHECKPOINT_SCHEMA}")
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        yield checkpointer, CaseStore(pool)
