"""API: roles and business rules (BR-08/09/10, UC-03/04/05, FR-109) with dev tokens, a real graph
state from stubbed agents (in-memory checkpointer) and a fake case store and Kafka. No services."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from sentinel import data
from sentinel.api.app import create_app
from sentinel.api.auth import dev_token
from sentinel.api.backend import Backend
from sentinel.events import ALERTS_TOPIC, DECISIONS_TOPIC, FOLLOWUPS_TOPIC
from sentinel.graph import compile_graph, initial_state, run_config
from sentinel.persistence import DECIDED_STATUSES
from tests import stubs

LABEL = {"evidence_complete": 4, "citations_accurate": 5, "recommendation_sound": 4, "narrative_clear": 3,
         "decision_correct": True, "comment": "fine"}


class FakeCases:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.label_rows: dict[tuple, dict] = {}

    def add(self, alert: dict, status: str, tier: str | None):
        assigned = "l2" if status == "awaiting_approval" else ("l1" if tier == "fast" else "l2")  # as the worker does
        self.rows[alert["case_id"]] = {"case_id": alert["case_id"], "status": status, "tier": tier,
                                       "assigned_role": assigned, "qa_flagged": False,
                                       "legal_entity": alert["legal_entity"], "customer_id": alert["customer_id"],
                                       "thread_id": alert["case_id"], "alert": alert, "updated_at": "2026-10-08T10:00:00Z",
                                       "scenario": alert["scenario_name"]}

    async def get(self, case_id):
        return self.rows.get(case_id)

    async def queue(self, statuses=None, tier=None, legal_entity=None, limit=200, roles=None, decided_only=False):
        return [r for r in self.rows.values() if (not statuses or r["status"] in statuses)
                and (not tier or r["tier"] == tier) and (not legal_entity or r["legal_entity"] == legal_entity)
                and (roles is None or r["assigned_role"] in roles)
                and (not decided_only or r["status"] in DECIDED_STATUSES)][:limit]

    async def qa_sample(self, reviewer, n):
        return [r for r in self.rows.values() if r["status"] in DECIDED_STATUSES
                and (r["case_id"], reviewer) not in self.label_rows][:n]

    async def save_label(self, case_id, reviewer, label):
        self.label_rows[(case_id, reviewer)] = label

    async def labels(self, case_id):
        return [{"reviewer": r, "label": lab} for (c, r), lab in self.label_rows.items() if c == case_id]


@pytest.fixture
def api():
    """Client factory plus what the API published. CASE-0001 is paused at review (fast lane),
    CASE-0003 at review (full lane), CASE-0002 at the approval of a customer request."""
    cases, sent = FakeCases(), []
    plain_narrative = stubs.narrative(["txn:TXN-1006"])

    async def kyc(state):  # PII: raw evidence carries the real value, the model's output a token
        return {"evidence": [{"id": "crm:N9", "source": "crm", "agent": "kyc", "summary": "Jordan Ellis called"}],
                "findings": {"kyc": {"stub": True}}, "pii_vault": {"<CUSTOMER_NAME_abc123>": "Jordan Ellis"}}

    async def narrative(state):
        out = await plain_narrative(state)
        out["narrative"]["summary"] = "Deposits by <CUSTOMER_NAME_abc123>"
        return out

    graph = compile_graph(nodes=stubs.nodes(kyc=kyc, narrative=narrative))
    approval_graph = compile_graph(nodes=stubs.request_info_nodes())

    async def setup():
        for cid, g in (("CASE-0001", graph), ("CASE-0003", graph), ("CASE-0002", approval_graph)):
            await g.ainvoke(initial_state(data.get_alert(cid)), run_config(cid))

    asyncio.run(setup())
    for cid, status in (("CASE-0001", "awaiting_review"), ("CASE-0003", "awaiting_review"),
                        ("CASE-0002", "awaiting_approval")):
        g = approval_graph if cid == "CASE-0002" else graph
        tier = asyncio.run(g.aget_state(run_config(cid))).values["tier"]
        cases.add(data.get_alert(cid), status, tier)

    class Graphs:  # route each thread to the graph that ran it
        async def aget_state(self, config):
            g = approval_graph if config["configurable"]["thread_id"] == "CASE-0002" else graph
            return await g.aget_state(config)

        def aget_state_history(self, config):
            return graph.aget_state_history(config)

    async def send(topic, event):
        sent.append((topic, event))

    async def tail(case_id):
        for event in ({"case_id": case_id, "type": "case_started", "at": "1"},
                      {"case_id": case_id, "type": "awaiting_review", "at": "2"}):
            yield event

    async def ready():
        return {"postgres": True, "kafka": True}

    async def events(case_id):
        return [event async for event in tail(case_id)]

    app = create_app(Backend(cases, Graphs(), send, tail, ready, events))

    def client(role: str | None) -> TestClient:
        c = TestClient(app)
        if role:
            c.headers["Authorization"] = f"Bearer {dev_token(f'{role}.user', [role])}"
        return c

    client.sent, client.cases, client.graph = sent, cases, graph
    return client


def test_ops_and_auth(api):
    with api(None) as c:
        assert c.get("/healthz").json() == {"status": "ok"}
        assert c.get("/readyz").json() == {"postgres": True, "kafka": True}
        assert "sentinel_api_requests_total" in c.get("/metrics").text
        assert c.get("/cases").status_code == 401
        c.headers["Authorization"] = "Bearer not-a-token"
        assert c.get("/cases").status_code == 401
    with api("qa") as c:
        assert c.get("/me").json() == {"username": "qa.user", "roles": ["qa"], "sees_pii": False}


def test_workbench_sign_in_config_and_dev_tokens(api, monkeypatch):
    from sentinel.config import settings

    with api(None) as c:
        assert c.get("/auth/config").json()["mode"] == "dev"
        token = c.post("/auth/dev-token", json={"role": "l2"}).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {token}"
        assert c.get("/me").json()["roles"] == ["l2"]
        assert c.post("/auth/dev-token", json={"role": "boss"}).status_code == 422
        monkeypatch.setattr(settings, "auth_mode", "cognito")
        monkeypatch.setattr(settings, "cognito_user_pool_id", "eu-west-2_pool")
        assert c.post("/auth/dev-token", json={"role": "l2"}).status_code == 404  # never alongside Cognito
        config = c.get("/auth/config").json()
        assert config["mode"] == "cognito" and config["authority"].endswith("/eu-west-2_pool")
        assert config["demo_role_switch"] is False
        assert c.post("/auth/demo-sign-in", json={"role": "l2"}).status_code == 404  # off unless switched on
        monkeypatch.setattr(settings, "demo_role_switch", True)
        monkeypatch.setattr("sentinel.api.app.token_for", lambda role: f"cognito-token-for-{role}")
        assert c.post("/auth/demo-sign-in", json={"role": "qa"}).json() == {
            "access_token": "cognito-token-for-qa", "username": "qa.reviewer"}


def test_network_graph(api):
    with api("l1") as c:
        graph = c.get("/cases/CASE-0001/network").json()
        assert graph["customer_id"] == "CUST-00042" and graph["nodes"][0]["case_customer"]


def test_blind_mode_hides_the_draft_until_decided(api, monkeypatch):
    from sentinel.config import settings

    monkeypatch.setattr(settings, "blind_mode_percent", 100)
    with api("l1") as c:
        case = c.get("/cases/CASE-0001").json()
        assert case["blind"] and case["recommendation"] is None and "narrative" not in case["state"]
        assert "recommendation" not in case["state"]["findings"]["typology"]
        assert c.post("/cases/CASE-0001/decision", json={"action": "escalate", "reason_code": "STRUCTURING_CONFIRMED"}
                      ).status_code == 202
    assert api.sent[0][1].agree_with_recommendation is True  # agreement still measured against the hidden draft
    api.cases.rows["CASE-0001"]["status"] = "escalated"
    with api("l1") as c:
        assert "narrative" in c.get("/cases/CASE-0001").json()["state"]  # shown once decided


def test_queues_show_each_level_only_its_cases(api):
    """BR-08/BR-16, BR-17: fast lane with L1, full lane and approvals with L2; no level sees another's cases."""
    with api("l1") as c:
        assert [r["case_id"] for r in c.get("/cases").json()] == ["CASE-0001"]
        assert c.get("/cases/CASE-0003").status_code == 404  # not disclosed
    with api("l2") as c:
        assert {r["case_id"] for r in c.get("/cases").json()} == {"CASE-0002", "CASE-0003"}
        assert [r["case_id"] for r in c.get("/cases", params={"lane": "full"}).json()] == ["CASE-0003"]
        assert c.get("/cases/CASE-0001").status_code == 404
    with api("mlro") as c:
        assert c.get("/cases").json() == []
    with api("qa") as c:
        assert c.get("/cases").json() == []  # nothing decided yet
    with api("admin") as c:
        assert len(c.get("/cases").json()) == 3


def test_case_view_restores_pii_only_for_allowed_roles(api):
    with api("l1") as c:
        case = c.get("/cases/CASE-0001").json()
        assert case["waiting_for"]["kind"] == "decision" and case["recommendation"]["recommendation"] == "escalate"
        assert case["state"]["narrative"]["summary"] == "Deposits by Jordan Ellis"
        assert "pii_vault" not in case["state"] and case["state"]["pii_values_redacted"] == 1
        assert [e["type"] for e in c.get("/cases/CASE-0001/events").json()] == ["case_started", "awaiting_review"]
        assert c.get("/cases/CASE-NOPE").status_code == 404
    api.cases.rows["CASE-0001"]["status"] = "closed"  # QA reviewers see decided cases
    with api("qa") as c:  # FR-109: tokens stay, and raw evidence is masked
        case = c.get("/cases/CASE-0001").json()
        assert case["state"]["narrative"]["summary"] == "Deposits by <CUSTOMER_NAME_abc123>"
        crm = next(e for e in case["state"]["evidence"] if e["id"] == "crm:N9")
        assert crm["summary"] == "<CUSTOMER_NAME_abc123> called"


def test_history_and_stream(api):
    with api("l1") as c:
        history = c.get("/cases/CASE-0001/history").json()
        assert history["thread_id"] == "CASE-0001" and history["checkpoints"][-1]["next"] == ["human_review"]
        assert history["checkpoints"][-1]["completed"] == ["qa"] and history["checkpoints"][0]["completed"] == []
        with c.stream("GET", "/cases/CASE-0001/stream") as resp:
            body = "".join(resp.iter_text())
        assert "event: case_started" in body and "event: awaiting_review" in body


def test_decision_rules(api):
    with api("l1") as c:
        ok = c.post("/cases/CASE-0001/decision", json={"action": "escalate", "reason_code": "STRUCTURING_CONFIRMED"})
        assert ok.status_code == 202
        # BR-08: a full-lane case is with L2, so it does not exist for an L1 analyst
        assert c.post("/cases/CASE-0003/decision",
                      json={"action": "close", "reason_code": "FP_DATA_ERROR"}).status_code == 404
        # Only the MLRO files SARs
        assert c.post("/cases/CASE-0001/decision",
                      json={"action": "file_sar", "reason_code": "SUSPICION_CONFIRMED"}).status_code == 422
        # BR-09: the reason code must belong to the action
        assert c.post("/cases/CASE-0001/decision",
                      json={"action": "close", "reason_code": "STRUCTURING_CONFIRMED"}).status_code == 422
    with api("l2") as c:
        # BR-10: only while awaiting review (CASE-0002 waits for the approval of a customer request)
        assert c.post("/cases/CASE-0002/decision",
                      json={"action": "close", "reason_code": "FP_DATA_ERROR"}).status_code == 409
        assert c.post("/cases/CASE-0003/decision",
                      json={"action": "escalate", "reason_code": "SANCTIONS_TRUE_MATCH"}).status_code == 202
    with api("qa") as c:
        assert c.post("/cases/CASE-0001/decision",
                      json={"action": "close", "reason_code": "FP_DATA_ERROR"}).status_code == 403
    topic, event = api.sent[0]
    assert topic == DECISIONS_TOPIC and event.investigator_id == "l1.user"  # from the token, not the body
    assert event.agree_with_recommendation is True
    assert len(api.sent) == 2


def test_approval_and_reply(api):
    with api("l1") as c:  # UC-03: approval is for L2
        assert c.post("/cases/CASE-0002/approval", json={"action": "approve"}).status_code == 403
    with api("l2") as c:
        draft = c.get("/cases/CASE-0002").json()["waiting_for"]
        assert draft["kind"] == "approval" and draft["draft"]["rail"]["passed"]
        bad = c.post("/cases/CASE-0002/approval", json={"action": "edit", "message": "This is a suspicious activity check."})
        assert bad.status_code == 422 and "tip off" in bad.json()["detail"]
        assert c.post("/cases/CASE-0002/approval", json={"action": "approve"}).status_code == 202
        assert c.post("/cases/CASE-0003/approval", json={"action": "approve"}).status_code == 409
        # UC-04: replies only for cases that requested information
        assert c.post("/cases/CASE-0002/reply", json={"reply_text": "Car sale"}).status_code == 409
        # After approval the fast-lane case went back to L1, which asked the customer: L1 attaches the reply
        api.cases.rows["CASE-0002"].update(status="info_requested", assigned_role="l1")
        assert c.post("/cases/CASE-0002/reply", json={"reply_text": "Car sale"}).status_code == 404
    with api("l1") as c:
        assert c.post("/cases/CASE-0002/reply", json={"reply_text": "Car sale"}).status_code == 202
    assert [t for t, _ in api.sent] == [DECISIONS_TOPIC, FOLLOWUPS_TOPIC]
    assert api.sent[0][1].approver_id == "l2.user"


def test_start_case_roles(api):
    alert = data.get_alert("CASE-0001")
    with api("l1") as c:
        assert c.post("/cases", json=alert).status_code == 403
    with api("admin") as c:
        assert c.post("/cases", json=alert).status_code == 202
        assert c.post("/cases", json={**alert, "score": 7}).status_code == 422  # schema-validated
    assert api.sent[0][0] == ALERTS_TOPIC


def test_qa_sampling_and_labels(api):
    api.cases.rows["CASE-0001"]["status"] = "escalated"
    with api("l2") as c:
        assert c.get("/qa/sample").status_code == 403
    with api("qa") as c:
        assert [r["case_id"] for r in c.get("/qa/sample").json()] == ["CASE-0001"]
        assert c.post("/cases/CASE-0003/qa-label", json=LABEL).status_code == 409  # not decided yet
        assert c.post("/cases/CASE-0001/qa-label", json={**LABEL, "narrative_clear": 9}).status_code == 422
        assert c.post("/cases/CASE-0001/qa-label", json=LABEL).status_code == 201
        assert c.get("/qa/sample").json() == []  # labelled cases leave this reviewer's sample
        assert c.get("/cases/CASE-0001/qa-labels").json()[0]["reviewer"] == "qa.user"


def test_escalation_up_to_the_mlro(api):
    """BR-16: L2 escalates a full-lane case; it is then with the MLRO only, who files the SAR."""
    from langgraph.types import Command

    asyncio.run(api.graph.ainvoke(Command(resume={"action": "escalate", "reason_code": "SANCTIONS_TRUE_MATCH",
                                                  "investigator_id": "l2.user"}), run_config("CASE-0003")))
    api.cases.rows["CASE-0003"]["assigned_role"] = "mlro"  # what the worker records at the new pause
    with api("l2") as c:
        assert c.get("/cases/CASE-0003").status_code == 404
    with api("mlro") as c:
        case = c.get("/cases/CASE-0003").json()
        assert case["waiting_for"]["level"] == "mlro" and set(case["waiting_for"]["reason_codes"]) == {"file_sar", "no_sar"}
        assert [d["level"] for d in case["state"]["decisions"]] == ["l2"]  # the trail the MLRO sees
        assert c.post("/cases/CASE-0003/decision", json={"action": "escalate", "reason_code": "MULE_NETWORK"}
                      ).status_code == 422
        assert c.post("/cases/CASE-0003/decision", json={"action": "file_sar", "reason_code": "SANCTIONS_EXPOSURE"}
                      ).status_code == 202
    assert api.sent[-1][1].action == "file_sar" and api.sent[-1][1].investigator_id == "mlro.user"