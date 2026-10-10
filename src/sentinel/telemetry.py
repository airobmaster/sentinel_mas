"""Observability (TDD §14, FR-150/151, BR-14): OpenTelemetry traces to the collector (-> Tempo) and
Prometheus metrics.

Traces: a LangChain callback turns every graph run into spans: one per graph step (triage, kyc, ...),
model call (model, tokens) and tool call, nested as they ran and tagged with the case ID, under the
worker's span for the Kafka message. Nothing goes to LangSmith from here (BR-14); LangSmith tracing in
development is separate and switched on by its own environment variables.

Metrics: counters for case events, security events and tokens, exposed by the worker (`/metrics` on
port 9464) and the API (`/metrics`). Tracing is off unless SENTINEL_OTEL_ENDPOINT is set.
"""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode
from prometheus_client import Counter

from sentinel.config import settings

log = logging.getLogger("sentinel.telemetry")
tracer = trace.get_tracer("sentinel")

CASE_EVENTS = Counter("sentinel_case_events_total", "Case events published by the workers", ["type", "detail"])
SECURITY_EVENTS = Counter("sentinel_security_events_total", "Guardrail events (injections, denials, budgets)", ["kind"])
TOKENS = Counter("sentinel_model_tokens_total", "Model tokens used", ["agent", "model", "direction"])
MODEL_CALLS = Counter("sentinel_model_calls_total", "Model calls", ["agent", "model", "outcome"])
TOOL_CALLS = Counter("sentinel_tool_calls_total", "Tool calls", ["tool", "outcome"])

_configured = False
# The graph step running in this task (and its child tasks): the parent for model and tool calls whose LangChain
# parent run is unknown, e.g. runs that LangSmith's own tracing inserts around agent middleware
_STEP: ContextVar[tuple[Span, str] | None] = ContextVar("sentinel_step", default=None)


def setup(service_name: str) -> bool:
    """Send traces to the OTLP endpoint (if configured). Safe to call more than once."""
    global _configured
    if _configured or not settings.otel_endpoint:
        return _configured
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name,
                                                        "deployment.environment": settings.environment}))
    provider.add_span_processor(BatchSpanProcessor(
        OTLPSpanExporter(endpoint=f"{settings.otel_endpoint.rstrip('/')}/v1/traces")))
    trace.set_tracer_provider(provider)
    _configured = True
    log.info("tracing to %s as %s", settings.otel_endpoint, service_name)
    return True


@contextmanager
def case_span(name: str, case_id: str, **attributes):
    """The root span for handling one message about a case (the worker) or one request (scripts)."""
    with tracer.start_as_current_span(name, attributes={"sentinel.case_id": case_id, "sentinel.kind": "case",
                                                        **{f"sentinel.{k}": v for k, v in attributes.items()}}) as span:
        yield span


def record_case_event(event_type: str, detail: str | None = None, data: dict | None = None) -> None:
    """Count a case event; security events also by kind."""
    label = ""
    if event_type == "awaiting_review":
        label = (data or {}).get("level") or ""
    elif event_type == "decision_applied":
        label = f"{(data or {}).get('level', '')}:{(data or {}).get('action', '')}"
    CASE_EVENTS.labels(event_type, label).inc()
    if event_type == "security_event" and detail:
        SECURITY_EVENTS.labels(detail.split(":", 1)[0]).inc()


class TracingCallback(BaseCallbackHandler):
    """LangChain callbacks -> OpenTelemetry spans. Graph steps, model calls and tool calls become spans;
    everything in between (agent internals, runnables) only links children to the nearest span."""

    run_inline = True  # stay on the event loop, so the current OTel context (the worker span) is the parent

    def __init__(self, case_id: str):
        self.case_id = case_id
        self.spans: dict[UUID, Span] = {}  # run_id -> span opened for it
        self.parents: dict[UUID, Span | None] = {}  # run_id -> nearest span above it
        self.agents: dict[UUID, str] = {}  # run_id -> graph step it belongs to
        self.root = otel_context.get_current()

    # --- helpers -----------------------------------------------------------------------------
    def _parent(self, parent_run_id: UUID | None) -> Span | None:
        if parent_run_id is None:
            return None
        if parent_run_id in self.spans or parent_run_id in self.parents:
            return self.spans.get(parent_run_id) or self.parents.get(parent_run_id)
        step = _STEP.get()
        return step[0] if step else None

    def _agent(self, parent_run_id: UUID | None) -> str:
        if parent_run_id in self.agents:
            return self.agents[parent_run_id]
        step = _STEP.get()
        return step[1] if step else ""

    def _open(self, run_id: UUID, parent_run_id: UUID | None, name: str, kind: str, **attrs) -> Span:
        parent = self._parent(parent_run_id)
        ctx = trace.set_span_in_context(parent) if parent else self.root
        span = tracer.start_span(name, context=ctx, attributes={
            "sentinel.case_id": self.case_id, "sentinel.kind": kind,
            **{k: v for k, v in attrs.items() if v is not None}})
        self.spans[run_id] = span
        return span

    def _pass_through(self, run_id: UUID, parent_run_id: UUID | None) -> None:
        self.parents[run_id] = self._parent(parent_run_id)
        if parent_run_id in self.agents:
            self.agents[run_id] = self.agents[parent_run_id]

    def _close(self, run_id: UUID, error: BaseException | None = None) -> None:
        self.parents.pop(run_id, None)
        span = self.spans.pop(run_id, None)
        if span is None:
            return
        if error is not None:
            span.record_exception(error)
            span.set_status(Status(StatusCode.ERROR, f"{type(error).__name__}: {error}"[:200]))
        span.end()

    # --- chains: the graph and its steps -----------------------------------------------------
    def on_chain_start(self, serialized: dict | None, inputs: Any, *, run_id: UUID, parent_run_id: UUID | None = None,
                       metadata: dict | None = None, name: str | None = None, **kwargs) -> None:
        node = (metadata or {}).get("langgraph_node")
        if parent_run_id is None:  # the graph run itself
            self._open(run_id, None, "sentinel.graph", "graph")
        elif node and name == node and parent_run_id not in self.agents:  # a step of the outer graph
            self.agents[run_id] = node
            span = self._open(run_id, parent_run_id, f"step {node}", "node", **{"sentinel.node": node})
            _STEP.set((span, node))
        else:
            self._pass_through(run_id, parent_run_id)

    def on_chain_end(self, outputs: Any, *, run_id: UUID, **kwargs) -> None:
        self._close(run_id)

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs) -> None:
        self._close(run_id, error)

    # --- model calls ---------------------------------------------------------------------------
    def on_chat_model_start(self, serialized: dict | None, messages: Any, *, run_id: UUID,
                            parent_run_id: UUID | None = None, metadata: dict | None = None, **kwargs) -> None:
        model = (metadata or {}).get("ls_model_name") or (serialized or {}).get("name", "model")
        agent = self._agent(parent_run_id)
        self.agents[run_id] = agent
        self._open(run_id, parent_run_id, f"model {model}", "model",
                   **{"sentinel.node": agent, "gen_ai.request.model": model})

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs) -> None:
        span = self.spans.get(run_id)
        agent = self.agents.pop(run_id, "")
        model = span.attributes.get("gen_ai.request.model", "model") if span is not None else "model"
        usage = {}
        for generations in getattr(response, "generations", []) or []:
            for g in generations:
                usage = getattr(getattr(g, "message", None), "usage_metadata", None) or usage
        if span is not None and usage:
            span.set_attribute("gen_ai.usage.input_tokens", usage.get("input_tokens", 0))
            span.set_attribute("gen_ai.usage.output_tokens", usage.get("output_tokens", 0))
        TOKENS.labels(agent, model, "input").inc(usage.get("input_tokens", 0))
        TOKENS.labels(agent, model, "output").inc(usage.get("output_tokens", 0))
        MODEL_CALLS.labels(agent, model, "ok").inc()
        self._close(run_id)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs) -> None:
        span = self.spans.get(run_id)
        model = span.attributes.get("gen_ai.request.model", "model") if span is not None else "model"
        MODEL_CALLS.labels(self.agents.pop(run_id, ""), model, "error").inc()
        self._close(run_id, error)

    # --- tool calls ----------------------------------------------------------------------------
    def on_tool_start(self, serialized: dict | None, input_str: str, *, run_id: UUID,
                      parent_run_id: UUID | None = None, **kwargs) -> None:
        tool = (serialized or {}).get("name") or kwargs.get("name") or "tool"
        self._open(run_id, parent_run_id, f"tool {tool}", "tool",
                   **{"sentinel.node": self._agent(parent_run_id), "sentinel.tool": tool})

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs) -> None:
        span = self.spans.get(run_id)
        if span is not None:
            TOOL_CALLS.labels(span.attributes.get("sentinel.tool", "tool"), "ok").inc()
        self._close(run_id)

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs) -> None:
        span = self.spans.get(run_id)
        if span is not None:
            TOOL_CALLS.labels(span.attributes.get("sentinel.tool", "tool"), "error").inc()
        self._close(run_id, error)


def callbacks_for(case_id: str) -> list:
    """Callbacks to attach to a graph run (none when tracing is off)."""
    return [TracingCallback(case_id)] if _configured else []
