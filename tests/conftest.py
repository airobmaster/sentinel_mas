"""Offline tests run on the JSON fixtures with in-process tools and no OPA, whatever .env says.
Tests marked `live` or `integration` use the configured settings (e.g. the Docker stack)."""

import pytest

from sentinel import data
from sentinel.agents import factory
from sentinel.config import REPO_ROOT, settings

FIXTURES = REPO_ROOT / "data" / "fixtures" / "cases.json"


@pytest.fixture(autouse=True)
def offline_settings(request, monkeypatch):
    if request.node.get_closest_marker("live") or request.node.get_closest_marker("integration"):
        yield
        return
    monkeypatch.setattr(settings, "data_backend", "json")
    monkeypatch.setattr(settings, "dataset_paths", [FIXTURES])
    monkeypatch.setattr(settings, "tool_mode", "local")
    monkeypatch.setattr(settings, "opa_url", None)
    data.backend.cache_clear()
    factory._specialists.clear()
    yield
    data.backend.cache_clear()
    factory._specialists.clear()
