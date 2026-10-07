"""Shared builder for specialist agents (TDD §6.1).

Each specialist runs in two phases:
1. a tool loop (`create_agent`, tool_choice=auto) in which the model gathers evidence and stops on its own;
2. one structured-output call that turns the conversation into the agent's Pydantic schema.
We do not use `response_format=ToolStrategy(...)`: it forces tool_choice="any" on every turn, and
DeepSeek on Bedrock then re-calls the data tools forever instead of the output tool.

Tools come from tools/registry.py: in-process (agent built once) or MCP (agent built per run on
sessions that stay open for the run). When settings.opa_url is set, every tool call is
authorised by OPA first. PII, rails and budget middleware are added in later slices.
"""

from dataclasses import dataclass, field

from langchain.agents import create_agent
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from sentinel.config import settings
from sentinel.middleware.opa import CASE_CONTEXT, opa_authorize
from sentinel.prompts import load_prompt
from sentinel.tools.registry import AGENT_TOOLS, tools_for

FINALISE = (
    "Now return your final output in the required structure. Use only facts from this conversation and "
    "cite evidence IDs exactly as they appear above. Never invent an ID."
)


@dataclass
class Specialist:
    name: str
    prompt: str
    model: ChatBedrockConverse
    extractor: object  # model bound to the output schema
    versions: dict  # recorded in state for audit
    _local_agent: object | None = field(default=None, repr=False)

    def build_agent(self, tools: list):
        middleware = [opa_authorize(self.name)] if settings.opa_url else []
        return create_agent(model=self.model, tools=tools, system_prompt=self.prompt,
                            middleware=middleware, name=self.name)


_specialists: dict[str, Specialist] = {}


def get_specialist(name: str, model_id: str, prompt_name: str, schema: type[BaseModel]) -> Specialist:
    if name not in _specialists:
        version, prompt = load_prompt(prompt_name)
        model = ChatBedrockConverse(model=model_id, region_name=settings.aws_region, temperature=0)
        _specialists[name] = Specialist(
            name=name, prompt=prompt, model=model, extractor=model.with_structured_output(schema),
            versions={"prompt": f"{prompt_name}@{version}", "model": model_id, "tools": settings.tool_mode},
        )
    return _specialists[name]


async def run_tool_loop(spec: Specialist, messages: list[BaseMessage]) -> list[BaseMessage]:
    config = {"recursion_limit": settings.agent_recursion_limit}
    if settings.tool_mode == "local":
        if spec._local_agent is None:
            async with tools_for(spec.name) as tools:
                spec._local_agent = spec.build_agent(tools)
        return (await spec._local_agent.ainvoke({"messages": messages}, config=config))["messages"]
    async with tools_for(spec.name) as tools:  # MCP sessions stay open for this run only
        return (await spec.build_agent(tools).ainvoke({"messages": messages}, config=config))["messages"]


async def run_specialist(spec: Specialist, brief: str, legal_entity: str,
                         extra_ids: list[str] | None = None) -> tuple[BaseModel, list[BaseMessage]]:
    """Return (structured output, conversation messages). `extra_ids`: evidence IDs given in the brief
    that the output may cite in addition to what the tools return."""
    CASE_CONTEXT.set({"legal_entity": legal_entity})  # read by the OPA middleware in this task only
    messages: list[BaseMessage] = [HumanMessage(brief)]
    finalise = FINALISE
    ids = list(extra_ids or [])
    if AGENT_TOOLS.get(spec.name):
        messages = await run_tool_loop(spec, messages)
        ids += [e["id"] for e in tool_evidence(messages, spec.name)]
    if ids:
        # Anchor the structured output to real IDs: in a long conversation models otherwise
        # "reconstruct" plausible-looking IDs from memory.
        finalise += f"\nEvidence IDs from the brief and your tool calls (copy them exactly): {', '.join(dict.fromkeys(ids))}"
    result = await spec.extractor.ainvoke([SystemMessage(spec.prompt), *messages, HumanMessage(finalise)])
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
