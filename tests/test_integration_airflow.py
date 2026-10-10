"""Airflow DAGs load in the running container, and the data jobs they call work against the stack.
Needs `docker compose up -d airflow`. Run with: pytest -m integration"""

import json
import socket
import subprocess

import psycopg
import pytest

from sentinel.config import settings
from sentinel.datagen.loader import SCREENING_LISTS, refresh_lists

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(socket.socket().connect_ex(("localhost", 8088)) != 0, reason="Airflow not running"),
]
DAGS = {"alert_replay", "nightly_eval", "sanctions_refresh", "graph_rebuild", "policy_reembed"}


def airflow(*args: str) -> str:
    return subprocess.run(["docker", "compose", "exec", "-T", "airflow", "airflow", *args],
                          capture_output=True, text=True, timeout=120, check=True).stdout


def test_dags_load_without_errors():
    assert "No data found" in airflow("dags", "list-import-errors")
    assert {d["dag_id"] for d in json.loads(airflow("dags", "list", "-o", "json"))} >= DAGS


def test_sentinel_cli_is_installed_for_the_tasks():
    out = subprocess.run(["docker", "compose", "exec", "-T", "airflow", "/opt/sentinel/bin/sentinel", "replay",
                          "select", "--split", "holdout", "--n", "3"], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and len(out.stdout.strip().splitlines()[-1].split(",")) == 3


def test_refresh_lists_replaces_the_lists():
    counts = refresh_lists(settings.dataset_paths, settings.pg_dsn)
    with psycopg.connect(settings.pg_dsn) as conn:
        for table in SCREENING_LISTS:
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == counts[table] > 0
