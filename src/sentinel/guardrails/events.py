"""Security events (injections caught, tools denied, budget hits), collected per specialist run.

Middleware runs inside an agent's tool loop and cannot write case state directly, so events are
gathered in a context variable that run_specialist sets and returns to the node."""

import logging
from contextvars import ContextVar
from datetime import UTC, datetime

log = logging.getLogger("sentinel.security")

SECURITY_EVENTS: ContextVar[list | None] = ContextVar("sentinel_security_events", default=None)


def record(kind: str, agent: str, detail: str, **data) -> dict:
    event = {"kind": kind, "agent": agent, "detail": detail, "data": data,
             "at": datetime.now(UTC).isoformat()}
    log.warning("security_event %s agent=%s %s", kind, agent, detail)
    events = SECURITY_EVENTS.get()
    if events is not None:
        events.append(event)
    return event
