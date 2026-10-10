"""Tracing: a graph run becomes nested spans (case -> graph -> steps -> model calls) tagged with the
case ID, and model tokens are counted. In-memory exporter, stubbed agents and a fake chat model."""

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from sentinel import data, telemetry
from sentinel.graph import compile_graph, initial_state, run_config
from tests import stubs

EXPORTER = InMemorySpanExporter()
_attached: list = []


@pytest.fixture
def tracing(monkeypatch):
    # The global provider can be set only once per process (another library may have set it already):
    # attach the in-memory exporter to whichever SDK provider is active
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        trace.set_tracer_provider(TracerProvider())
    provider = trace.get_tracer_provider()
    if provider not in _attached:
        provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
        _attached.append(provider)
    monkeypatch.setattr(telemetry, "_configured", True)
    EXPORTER.clear()
    yield EXPORTER


async def test_graph_run_becomes_nested_spans_with_case_id_and_tokens(tracing):
    model = GenericFakeChatModel(messages=iter([AIMessage(
        "summary", usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150})]))
    plain = stubs.narrative(["txn:TXN-1006"])

    async def narrative(state):  # a step that calls a chat model, like the real agents
        await model.ainvoke("draft the narrative")
        return await plain(state)

    graph = compile_graph(nodes=stubs.nodes(narrative=narrative))
    tokens = {k: v for k, v in telemetry.TOKENS._metrics.items()}  # (agent, model, direction) -> counter
    before = {k: v._value.get() for k, v in tokens.items()}
    with telemetry.case_span("sentinel.handle_alert", "CASE-0001"):
        await graph.ainvoke(initial_state(data.get_alert("CASE-0001")), run_config("CASE-0001"))

    spans = {s.name: s for s in tracing.get_finished_spans()}
    assert {"sentinel.handle_alert", "sentinel.graph", "step triage", "step kyc", "step narrative", "step qa",
            "step human_review"} <= set(spans)
    assert all(s.attributes["sentinel.case_id"] == "CASE-0001" for s in spans.values())
    # Nesting: handle_alert -> graph -> step narrative -> model call
    model_span = next(s for s in spans.values() if s.attributes["sentinel.kind"] == "model")
    assert model_span.parent.span_id == spans["step narrative"].context.span_id
    assert spans["step narrative"].parent.span_id == spans["sentinel.graph"].context.span_id
    assert spans["sentinel.graph"].parent.span_id == spans["sentinel.handle_alert"].context.span_id
    assert model_span.attributes["sentinel.node"] == "narrative" and model_span.attributes["gen_ai.usage.input_tokens"] == 120
    assert spans["step narrative"].attributes["sentinel.node"] == "narrative"
    model = model_span.attributes["gen_ai.request.model"]
    counted = telemetry.TOKENS.labels("narrative", model, "input")._value.get()
    assert counted == before.get(("narrative", model, "input"), 0) + 120


def test_no_callbacks_when_tracing_is_off(monkeypatch):
    monkeypatch.setattr(telemetry, "_configured", False)
    assert run_config("CASE-0001")["callbacks"] == []


def test_case_events_are_counted():
    before = telemetry.CASE_EVENTS.labels("decision_applied", "l1:escalate")._value.get()
    telemetry.record_case_event("decision_applied", data={"level": "l1", "action": "escalate"})
    assert telemetry.CASE_EVENTS.labels("decision_applied", "l1:escalate")._value.get() == before + 1
    before = telemetry.SECURITY_EVENTS.labels("injection_detected")._value.get()
    telemetry.record_case_event("security_event", "injection_detected: get_crm_notes returned instruction-like text")
    assert telemetry.SECURITY_EVENTS.labels("injection_detected")._value.get() == before + 1


def test_calls_with_unknown_parents_attach_to_the_running_step(tracing):
    """LangSmith's own tracing inserts runs around agent middleware that never reach LangChain callbacks: model
    and tool calls under them must still land in the step's span, not in a trace of their own."""
    from uuid import uuid4

    cb = telemetry.TracingCallback("CASE-0001")
    graph_run, step_run, model_run, tool_run = uuid4(), uuid4(), uuid4(), uuid4()
    cb.on_chain_start({}, {}, run_id=graph_run, parent_run_id=None, name="LangGraph")
    cb.on_chain_start({}, {}, run_id=step_run, parent_run_id=graph_run, name="kyc", metadata={"langgraph_node": "kyc"})
    cb.on_chat_model_start({"name": "ChatBedrockConverse"}, [], run_id=model_run, parent_run_id=uuid4())  # unknown
    cb.on_llm_end(None, run_id=model_run)
    cb.on_tool_start({"name": "get_customer_profile"}, "", run_id=tool_run, parent_run_id=uuid4())  # unknown
    cb.on_tool_end("", run_id=tool_run)
    cb.on_chain_end({}, run_id=step_run)
    cb.on_chain_end({}, run_id=graph_run)
    spans = {s.name: s for s in tracing.get_finished_spans()}
    step = spans["step kyc"]
    for name in ("model ChatBedrockConverse", "tool get_customer_profile"):
        assert spans[name].parent.span_id == step.context.span_id and spans[name].attributes["sentinel.node"] == "kyc"
    assert len({s.context.trace_id for s in spans.values()}) == 1
