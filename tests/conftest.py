"""Offline tests run on the JSON fixtures with in-process tools and no OPA, whatever .env says.
Tests marked `live` or `integration` use the configured settings (e.g. the Docker stack)."""

import asyncio
import os
import sys

os.environ.setdefault("LANGSMITH_TRACING", "false")  # tests never send traces to LangSmith (set before sentinel loads .env)

import pytest  # noqa: E402

from sentinel import data
from sentinel.agents import factory
from sentinel.config import REPO_ROOT, settings

FIXTURES = REPO_ROOT / "data" / "fixtures" / "cases.json"


def pytest_asyncio_loop_factories(config, item):
    """psycopg's async driver (Postgres checkpointer) cannot use Windows' default Proactor loop."""
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


@pytest.fixture(autouse=True)
def offline_settings(request, monkeypatch):
    if request.node.get_closest_marker("live") or request.node.get_closest_marker("integration"):
        yield
        return
    monkeypatch.setattr(settings, "data_backend", "json")
    monkeypatch.setattr(settings, "dataset_paths", [FIXTURES])
    monkeypatch.setattr(settings, "tool_mode", "local")
    monkeypatch.setattr(settings, "opa_url", None)
    monkeypatch.setattr(settings, "qa_llm_critic", False)
    monkeypatch.setattr(settings, "neo4j_uri", None)
    monkeypatch.setattr(settings, "presidio_url", None)  # pattern rules only: deterministic, no service
    monkeypatch.setattr(settings, "auth_mode", "dev")  # API tokens signed locally, no Cognito
    reset_caches()
    yield
    reset_caches()


def reset_caches() -> None:
    from sentinel import graphdb, kb

    data.backend.cache_clear()
    graphdb.network.cache_clear()
    kb.policy_kb.cache_clear()
    factory._specialists.clear()
