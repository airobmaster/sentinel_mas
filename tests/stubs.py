"""Stub agents for graph, worker and console tests (no Bedrock calls)."""

RECOMMENDATION = {"recommendation": "escalate", "reason_code": "STRUCTURING_CONFIRMED"}


def specialist(agent: str, eid: str):
    """Returns one evidence item and the agent's findings key."""

    async def node(state):
        return {"evidence": [{"id": eid, "source": "test", "agent": agent, "summary": f"{agent} evidence"}],
                "findings": {agent: {"stub": True}}}

    return node


async def typology(state):
    return {"evidence": [{"id": "policy:TEST@1#1", "source": "test", "agent": "typology", "summary": "policy"}],
            "findings": {"typology": {"typologies": [], "risk_score": 80, "rationale": "stub",
                                      "policy_refs": ["policy:TEST@1#1"], **RECOMMENDATION}}}


def narrative(citations: list[str]):
    """Narrative stub that cites citations[n] on its n-th call (last one repeats); `.calls` records them."""
    calls = []

    async def node(state):
        cite = citations[min(len(calls), len(citations) - 1)]
        calls.append(cite)
        return {"narrative": {"summary": "s", "claims": [{"text": "Five cash deposits below threshold",
                                                          "evidence_ids": [cite]}],
                              "open_questions": [], **RECOMMENDATION}}

    node.calls = calls
    return node


def nodes(**overrides) -> dict:
    """A full set of stubbed agent nodes for compile_graph(nodes=...)."""
    return {"kyc": specialist("kyc", "crm:N1"), "txn": specialist("txn", "txn:TXN-1006"),
            "screening": specialist("screening", "list:OFSI:X"), "network": specialist("network", "graph:C1"),
            "typology": typology, "narrative": narrative(["txn:TXN-1006"]), **overrides}
