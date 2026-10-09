"""Kafka round-trip tests start their own worker with stubbed agents. A real worker already running
(e.g. from start_sentinel.bat) joins the same consumer groups and would take the test's messages and
run real agents on them, so those tests skip while one is running. Workers run on the host until the
AWS slice, so a process check is enough (aiokafka cannot decode this broker's group descriptions)."""

import os

import psutil
import pytest


def worker_running() -> bool:
    for proc in psutil.process_iter(["pid", "cmdline"]):
        cmd = [part.lower() for part in (proc.info["cmdline"] or [])]
        if proc.info["pid"] != os.getpid() and any("sentinel" in c for c in cmd[:2]) and "worker" in cmd:
            return True
    return False


def skip_if_worker_running() -> None:
    if worker_running():
        pytest.skip("a Sentinel worker is already running (it would take the test's messages); stop it first")
