"""Shared builder for specialist agents (TDD §6.1).

Each specialist runs in two phases:
1. a tool loop (`create_agent`, tool_choice=auto) in which the model gathers evidence and stops on its own;
2. one structured-output call that turns the conversation into the agent's Pydantic schema.
We do not use `response_format=ToolStrategy(...)`: it forces tool_choice="any" on every turn, and
DeepSeek on Bedrock then re-calls the data tools forever instead of the output tool.

Tools come from tools/registry.py (in-process or MCP). When settings.opa_url is set, every
tool call is authorised by OPA first. PII, rails and budget middleware are added in later slices.
"""

from dataclasses import dataclass

from langchain.agents import create_agent
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from sentinel.config import settings
from sentinel.middleware.opa import CASE_CONTEXT, opa_authorize
from sentinel.prompts import load_prompt
from sentinel.tools.registry import tools_for

FINALISE = (
    "Now return your final output in the required structure. Use only facts from this conversation and "
    "cite evidence IDs exactly as they appear above. Never invent an ID."
)


@dataclass(frozen=True)
class Specialist:
    name: str
    prompt: str
    agent: object | None  # tool loop; None for agents without tools
    extractor: object  # model bound to the output schema
    versions: dict  # recorded in state for audit


_specialists: dict[str, Specialist] = {}


def build_specialist(name: str, model_id: str, tools: list, prompt_name: str, schema: type[BaseModel]) -> Specialist:
    version, prompt = load_prompt(prompt_name)
    model = ChatBedrockConverse(model=model_id, region_name=settings.aws_region, temperature=0)
    middleware = [opa_authorize(name)] if settings.opa_url else []
    agent = (
        create_agent(model=model, tools=tools, system_prompt=prompt, middleware=middleware, name=name)
        if tools
        else None
    )
    return Specialist(
        name=name,
        prompt=prompt,
        agent=agent,
        extractor=model.with_structured_output(schema),
        versions={"prompt": f"{prompt_name}@{version}", "model": model_id, "tools": settings.tool_mode},
    )


async def get_specialist(name: str, model_id: str, prompt_name: str, schema: type[BaseModel]) -> Specialist:
    """Build once per process; tool loading is async because MCP tools are discovered over HTTP."""
    if name not in _specialists:
        _specialists[name] = build_specialist(name, model_id, await tools_for(name), prompt_name, schema)
    return _specialists[name]


async def run_specialist(spec: Specialist, brief: str, legal_entity: str) -> tuple[BaseModel, list[BaseMessage]]:
    """Return (structured output, conversation messages)."""
    CASE_CONTEXT.set({"legal_entity": legal_entity})  # read by the OPA middleware in this task only
    messages: list[BaseMessage] = [HumanMessage(brief)]
    if spec.agent is not None:
        out = await spec.agent.ainvoke(
            {"messages": messages}, config={"recursion_limit": settings.agent_recursion_limit}
        )
        messages = out["messages"]
    result = await spec.extractor.ainvoke([SystemMessage(spec.prompt), *messages, HumanMessage(FINALISE)])
    return result, messages


def artifact_evidence(artifact) -> list[dict]:
    """Local tools attach the evidence list; MCP tools carry it in structured content."""
    if isinstance(artifact, list):
        return artifact
    if isinstance(artifact, dict):
        return (artifact.get("structured_content") or {}).get("evidence", [])
    return []


def tool_evidence(messages: list[BaseMessage], agent: str) -> list[dict]:
    """Evidence the agent's tool calls actually returned (from ToolMessage artifacts)."""
    return [
        {**e, "agent": agent}
        for m in messages
        if isinstance(m, ToolMessage) and m.status != "error"
        for e in artifact_evidence(m.artifact)
    ]
