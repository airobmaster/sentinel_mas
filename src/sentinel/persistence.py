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
# Final statuses after a decision ("escalate" is final only for cases decided before the review levels:
# now it moves the case to the next level, which stays awaiting_review)
STATUS_AFTER_DECISION = {"close": "closed", "escalate": "escalated", "request_info": "info_requested",
                         "file_sar": "sar_filed", "no_sar": "closed"}
DECIDED_STATUSES = ("closed", "escalated", "info_requested", "sar_filed")
OPEN_STATUSES = ("new", "in_progress", "awaiting_approval", "awaiting_review")
QA_SAMPLE_PERCENT = 10  # UC-05: cases whose automated QA passed, sampled for the QA reviewers

# Idempotent additions to schema.sql, so a database loaded before they existed keeps working
MIGRATIONS = (
    "ALTER TABLE cases.alerts ADD COLUMN IF NOT EXISTS tier text",
    """CREATE TABLE IF NOT EXISTS cases.qa_labels (
           case_id     text NOT NULL REFERENCES cases.alerts ON DELETE CASCADE,
           reviewer    text NOT NULL,
           label       jsonb NOT NULL,
           labelled_at timestamptz NOT NULL DEFAULT now(),
           PRIMARY KEY (case_id, reviewer))""",
    # Evaluation runs (sentinel eval, Airflow alert_replay); kept outside the reloadable data schemas
    "CREATE SCHEMA IF NOT EXISTS evals",
    """CREATE TABLE IF NOT EXISTS evals.runs (
           run_id     text PRIMARY KEY,
           created_at timestamptz NOT NULL,
           source     text NOT NULL,
           split      text,
           metrics    jsonb NOT NULL,
           cases      jsonb NOT NULL)""",
    # Review levels (BR-08, BR-16): who the case is with; whether its automated QA raised issues (UC-05)
    "ALTER TABLE cases.alerts ADD COLUMN IF NOT EXISTS assigned_role text",
    "ALTER TABLE cases.alerts ADD COLUMN IF NOT EXISTS qa_flagged boolean",
    """UPDATE cases.alerts SET assigned_role = CASE WHEN status = 'awaiting_approval' THEN 'l2'
                                                  WHEN tier = 'fast' THEN 'l1' ELSE 'l2' END
       WHERE assigned_role IS NULL AND tier IS NOT NULL""",
)
WAIT_DONE = ("awaiting_review", "awaiting_approval", "error", *STATUS_AFTER_DECISION.values())
ROW_COLUMNS = "case_id, status, tier, assigned_role, qa_flagged, legal_entity, customer_id"


class CaseStore:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool

    async def start(self, alert: dict, thread_id: str | None = None) -> None:
        """Record a case as in progress on a run thread (inserting it if the alert arrived only via Kafka)."""
        thread_id = thread_id or alert["case_id"]
        async with self.pool.connection() as conn:
            await conn.execute(
                """INSERT INTO cases.alerts (case_id, legal_entity, customer_id, alert, status, thread_id)
                   VALUES (%s, %s, %s, %s, 'in_progress', %s)
                   ON CONFLICT (case_id) DO UPDATE SET status = 'in_progress', thread_id = %s, updated_at = now()""",
                (alert["case_id"], alert["legal_entity"], alert["customer_id"], Jsonb(alert), thread_id, thread_id),
            )

    async def thread_for(self, case_id: str) -> str:
        """The case's current run thread (follow-up runs use case_id:rN)."""
        async with self.pool.connection() as conn:
            row = await (await conn.execute("SELECT thread_id FROM cases.alerts WHERE case_id = %s",
                                            (case_id,))).fetchone()
        return (row or {}).get("thread_id") or case_id

    async def save_reply(self, case_id: str, round_no: int, reply_text: str, received_at: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO cases.customer_replies (case_id, round, reply_text, received_at) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (case_id, round) DO NOTHING", (case_id, round_no, reply_text, received_at))

    async def set_status(self, case_id: str, status: str, tier: str | None = None, assigned_role: str | None = None,
                         qa_flagged: bool | None = None) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("UPDATE cases.alerts SET status = %s, tier = COALESCE(%s, tier), "
                               "assigned_role = COALESCE(%s, assigned_role), "
                               # once the automated QA has flagged a case it stays in the QA queue (BR-18)
                               "qa_flagged = COALESCE(qa_flagged, false) OR COALESCE(%s, false), "
                               "updated_at = now() WHERE case_id = %s", (status, tier, assigned_role, qa_flagged, case_id))

    async def assign(self, case_id: str, role: str) -> None:
        """The level that owns the case (set once triage knows the lane, and at each review level)."""
        async with self.pool.connection() as conn:
            await conn.execute("UPDATE cases.alerts SET assigned_role = %s WHERE case_id = %s", (role, case_id))

    async def get(self, case_id: str) -> dict | None:
        """The case-management record (status, lane, owner, current thread, alert)."""
        async with self.pool.connection() as conn:
            return await (await conn.execute(
                f"SELECT {ROW_COLUMNS}, thread_id, alert, updated_at FROM cases.alerts WHERE case_id = %s",
                (case_id,))).fetchone()

    async def queue(self, statuses: list[str] | None = None, tier: str | None = None,
                   legal_entity: str | None = None, limit: int = 200, roles: list[str] | None = None,
                   decided_only: bool = False) -> list[dict]:
        """The work queue, most recently updated first (FR-100 filters). `roles`: only cases assigned to one
        of these levels (BR-17: each level sees its own cases); None for administrators."""
        sql = (f"SELECT {ROW_COLUMNS}, alert->>'scenario_name' AS scenario, updated_at FROM cases.alerts WHERE true")
        params: list = []
        if roles is not None:
            sql += " AND assigned_role = ANY(%s)"
            params.append(roles)
        if decided_only:
            sql += " AND status = ANY(%s)"
            params.append(list(DECIDED_STATUSES))
        if statuses:
            sql += " AND status = ANY(%s)"
            params.append(statuses)
        if tier:
            sql += " AND tier = %s"
            params.append(tier)
        if legal_entity:
            sql += " AND legal_entity = %s"
            params.append(legal_entity)
        async with self.pool.connection() as conn:
            return await (await conn.execute(sql + " ORDER BY updated_at DESC, case_id LIMIT %s",
                                             (*params, limit))).fetchall()

    async def qa_sample(self, reviewer: str, n: int) -> list[dict]:
        """UC-05: decided cases for QA review, not yet labelled by this reviewer: every case whose automated QA
        raised issues, plus a stable 10% sample of the rest (hash of the case ID; BR-18). Flagged cases come first."""
        async with self.pool.connection() as conn:
            return await (await conn.execute(
                f"SELECT {ROW_COLUMNS}, alert->>'scenario_name' AS scenario, updated_at FROM cases.alerts a "
                "WHERE status = ANY(%s) AND (qa_flagged IS TRUE OR abs(hashtext(case_id)) %% 100 < %s) "
                "AND NOT EXISTS (SELECT 1 FROM cases.qa_labels l WHERE l.case_id = a.case_id AND l.reviewer = %s) "
                "ORDER BY qa_flagged IS TRUE DESC, updated_at DESC LIMIT %s",
                (list(DECIDED_STATUSES), QA_SAMPLE_PERCENT, reviewer, n))).fetchall()

    async def save_label(self, case_id: str, reviewer: str, label: dict) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO cases.qa_labels (case_id, reviewer, label) VALUES (%s, %s, %s) "
                "ON CONFLICT (case_id, reviewer) DO UPDATE SET label = EXCLUDED.label, labelled_at = now()",
                (case_id, reviewer, Jsonb(label)))

    async def labels(self, case_id: str) -> list[dict]:
        async with self.pool.connection() as conn:
            return await (await conn.execute(
                "SELECT reviewer, label, labelled_at FROM cases.qa_labels WHERE case_id = %s ORDER BY labelled_at",
                (case_id,))).fetchall()

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
            for statement in MIGRATIONS:
                await conn.execute(statement)
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


def case_thread(case_id: str) -> str:
    with psycopg.connect(settings.pg_dsn, **CONNECTION_KWARGS) as conn:
        row = conn.execute("SELECT thread_id FROM cases.alerts WHERE case_id = %s", (case_id,)).fetchone()
    return (row or {}).get("thread_id") or case_id


def migrate() -> None:
    with psycopg.connect(settings.pg_dsn, **CONNECTION_KWARGS) as conn:
        for statement in MIGRATIONS:
            conn.execute(statement)


def save_eval_run(report: dict) -> None:
    migrate()
    with psycopg.connect(settings.pg_dsn, **CONNECTION_KWARGS) as conn:
        conn.execute("INSERT INTO evals.runs (run_id, created_at, source, split, metrics, cases) "
                     "VALUES (%s, %s, %s, %s, %s, %s)",
                     (report["run_id"], report["created_at"], report["source"], report.get("split"),
                      Jsonb(report["metrics"]), Jsonb(report["cases"])))


def update_eval_run(run_id: str, metrics: dict, cases: list[dict]) -> None:
    """Add later scores (e.g. the narrative judge) to a stored evaluation run."""
    with psycopg.connect(settings.pg_dsn, **CONNECTION_KWARGS) as conn:
        conn.execute("UPDATE evals.runs SET metrics = %s, cases = %s WHERE run_id = %s",
                     (Jsonb(metrics), Jsonb(cases), run_id))


def wait_for_cases(case_ids: list[str], timeout: float, poll: float = 15) -> dict[str, str]:
    """Block until every case has paused for a human (or failed); returns case -> status."""
    import time

    deadline = time.time() + timeout
    while True:
        with psycopg.connect(settings.pg_dsn, **CONNECTION_KWARGS) as conn:
            rows = conn.execute("SELECT case_id, status FROM cases.alerts WHERE case_id = ANY(%s)",
                                (case_ids,)).fetchall()
        statuses = {r["case_id"]: r["status"] for r in rows}
        if all(statuses.get(c) in WAIT_DONE for c in case_ids) or time.time() > deadline:
            return statuses
        time.sleep(poll)


def reset_case(case_id: str) -> None:
    """Dev/test only: forget a case's runs (including follow-ups) so the alert can be investigated again."""
    with sync_checkpointer() as saver:
        threads = saver.conn.execute(
            "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id = %s OR thread_id LIKE %s",
            (case_id, f"{case_id}:r%")).fetchall()
        for row in threads:
            saver.delete_thread(row["thread_id"])
        saver.conn.execute("DELETE FROM cases.customer_replies WHERE case_id = %s", (case_id,))
        saver.conn.execute("UPDATE cases.alerts SET status = 'new', thread_id = NULL, assigned_role = NULL, "
                           "qa_flagged = NULL, updated_at = now() WHERE case_id = %s", (case_id,))
