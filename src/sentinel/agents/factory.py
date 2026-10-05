"""Shared builder for specialist agents (TDD §6.1).

Each specialist runs in two phases:
1. a tool loop (`create_agent`, tool_choice=auto) in which the model gathers evidence and stops on its own;
2. one structured-output call that turns the conversation into the agent's Pydantic schema.
We do not use `response_format=ToolStrategy(...)`: it forces tool_choice="any" on every turn, and
DeepSeek on Bedrock then re-calls the data tools forever instead of the output tool.

Middleware (OPA, PII, rails, budgets) is added in later slices.
"""

from dataclasses import dataclass

from langchain.agents import create_agent
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from sentinel.config import settings
from sentinel.prompts import load_prompt

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


def build_specialist(name: str, model_id: str, tools: list, prompt_name: str, schema: type[BaseModel]) -> Specialist:
    version, prompt = load_prompt(prompt_name)
    model = ChatBedrockConverse(model=model_id, region_name=settings.aws_region, temperature=0)
    agent = create_agent(model=model, tools=tools, system_prompt=prompt, name=name) if tools else None
    return Specialist(
        name=name,
        prompt=prompt,
        agent=agent,
        extractor=model.with_structured_output(schema),
        versions={"prompt": f"{prompt_name}@{version}", "model": model_id},
    )


async def run_specialist(spec: Specialist, brief: str) -> tuple[BaseModel, list[BaseMessage]]:
    """Return (structured output, conversation messages)."""
    messages: list[BaseMessage] = [HumanMessage(brief)]
    if spec.agent is not None:
        out = await spec.agent.ainvoke(
            {"messages": messages}, config={"recursion_limit": settings.agent_recursion_limit}
        )
        messages = out["messages"]
    result = await spec.extractor.ainvoke([SystemMessage(spec.prompt), *messages, HumanMessage(FINALISE)])
    return result, messages


def tool_evidence(messages: list[BaseMessage], agent: str) -> list[dict]:
    """Evidence the agent's tool calls actually returned (from ToolMessage artifacts)."""
    return [
        {**e, "agent": agent}
        for m in messages
        if isinstance(m, ToolMessage) and isinstance(m.artifact, list)
        for e in m.artifact
    ]
