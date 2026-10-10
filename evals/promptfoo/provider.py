"""Promptfoo provider for the Sentinel red team (FR-143). Two kinds of attack:

- injection: the attack text is planted as a CRM note of a low-risk customer (CASE-0002's customer) and the
  real KYC agent runs on it (Bedrock, in-process tools on the fixture data). Output: the agent's findings,
  the guardrail events and the tools it called.
- tipping_off: the attack text is an investigator's edit of a customer information request; output: what
  the tipping-off rail (BR-11) says about it.
"""

import asyncio
import json
import os
import sys

os.environ.setdefault("LANGSMITH_TRACING", "false")
os.environ["SENTINEL_DATA_BACKEND"] = "json"  # fixture data, so the attack note can be planted in memory
os.environ["SENTINEL_TOOL_MODE"] = "local"
os.environ.setdefault("SENTINEL_PRESIDIO_URL", "")

from sentinel import data  # noqa: E402
from sentinel.config import settings  # noqa: E402

CASE = "CASE-0002"


def run_injection(payload: str) -> dict:
    from sentinel.agents.kyc import kyc
    from sentinel.graph import initial_state

    settings.data_backend, settings.tool_mode, settings.opa_url = "json", "local", None
    alert = data.get_alert(CASE)
    customer = data.get_customer(alert["customer_id"])
    real_notes = data.crm_notes_for

    def notes_with_attack(customer_id: str) -> list[dict]:
        notes = real_notes(customer_id)
        if customer_id == alert["customer_id"]:
            notes = [*notes, {"note_id": "CRM-REDTEAM-01", "customer_id": customer_id, "date": "2026-03-30",
                              "text": payload}]
        return notes

    data.crm_notes_for = notes_with_attack
    state = initial_state(alert)
    state["tier"] = "fast"
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    out = asyncio.run(kyc(state))
    findings = (out.get("findings") or {}).get("kyc") or {}
    return {"customer_risk_rating": customer["risk_rating"], "findings": findings,
            "security_events": [e["kind"] for e in out.get("security_events") or []]}


def run_tipping_off(payload: str) -> dict:
    from sentinel.guardrails.output_rails import check_customer_text

    return {"violations": check_customer_text(payload)}


def call_api(prompt: str, options: dict, context: dict) -> dict:
    kind = (context.get("vars") or {}).get("kind", "injection")
    try:
        result = run_injection(prompt) if kind == "injection" else run_tipping_off(prompt)
        return {"output": json.dumps(result)}
    except Exception as e:  # noqa: BLE001 - reported as a provider error in the promptfoo results
        return {"error": f"{type(e).__name__}: {e}"}
