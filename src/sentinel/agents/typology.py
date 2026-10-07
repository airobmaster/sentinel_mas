"""Typology & policy specialist: maps findings to typologies and the written procedure, and makes
the case recommendation that the narrative explains and the investigator reviews.

Policy sections relevant to the findings are retrieved in code first and put in the brief, so the
assessment always rests on real, citable policy IDs; the agent can still search for more. (Left to
itself, the model sometimes skips the search and invents plausible-looking references.)"""

import asyncio

from sentinel.agents.briefs import brief_for
from sentinel.agents.factory import get_specialist, run_specialist, tool_evidence
from sentinel.config import settings
from sentinel.schemas import TypologyAssessment
from sentinel.state import CaseState
from sentinel.tools.policy_tools import search_policy

RED_FLAG_TERMS = {"STRUCTURING": "structuring cash deposits below threshold", "RAPID_OUTFLOW": "rapid movement of funds",
                  "FAN_IN": "mule many senders", "HIGH_RISK_JURISDICTION": "high-risk jurisdiction",
                  "HIGH_CASH_RATIO": "cash deposits", "PROFILE_MISMATCH": "activity above expected profile"}


def policy_query(state: CaseState) -> str:
    findings = state.get("findings", {})
    terms = [state["alert"]["scenario_name"]]
    terms += [RED_FLAG_TERMS.get(f["code"], f["code"].lower()) for f in (findings.get("txn") or {}).get("red_flags", [])]
    screening = findings.get("screening") or {}
    if any(h.get("is_true_match") for h in screening.get("hits", [])):
        terms.append("sanctions PEP true match")
    elif screening.get("hits"):
        terms.append("sanctions name match discounted false match")
    if (findings.get("network") or {}).get("assessment") == "mule_network_suspected":
        terms.append("mule shared device network")
    return "; ".join(terms)


async def typology(state: CaseState) -> dict:
    spec = get_specialist("typology", settings.model_typology, "typology", TypologyAssessment)
    content, policy = await asyncio.to_thread(search_policy, state["legal_entity"], policy_query(state), "", 6)
    policy = [{**e, "agent": "typology"} for e in policy]
    brief = (brief_for("typology", state) + "\n\nRelevant policy sections (already retrieved for you; cite these "
             f"IDs, and search for more if needed):\n{content}")
    assessment, messages = await run_specialist(spec, brief, state["legal_entity"],
                                                extra_ids=[e["id"] for e in policy])
    return {
        "evidence": policy + tool_evidence(messages, "typology"),
        "findings": {"typology": assessment.model_dump()},
        "versions": {"typology": spec.versions},
    }
