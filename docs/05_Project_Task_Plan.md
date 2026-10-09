# Sentinel — Project Task Plan (Living Checklist, 1-Week Build)

| Item | Value |
|---|---|
| Document | 05 — Project Task Plan |
| Version | 0.9 (vertical slices; full agent squad; DeepSeek + Claude Haiku critic on Bedrock) |
| Owner | Rakesh Velayudhan, AI pair-programming |
| Window | 7 days: Day 1 → Day 7 (demo at the end of Day 7) |
| Current day | Day 3: Slices 1–5 done (see [Build approach](#build-approach-vertical-slices)) |
| Related | [01 HLD](01_High_Level_Design.md) · [02 TDD](02_Technical_Design.md) · [03 Functional Spec](03_Functional_Specification.md) · [04 Roadmap](04_Project_Roadmap.md) |

---

## How to use this document

1. **Tick tasks** as they finish: change `- [ ]` to `- [x]` and note the day it finished, e.g. `✔ Day 1`.
2. **In progress:** add `` `WIP` `` after the task ID. **Blocked:** add `` `BLOCKED: <reason>` `` and log it in [Blockers & issues](#blockers--issues).
3. **End of each day:** update the [progress summary](#progress-summary), the [gate log](#gate-log) and the [daily log](#daily-log).
4. **If behind:** drop only **(S)** items, in the order of the [roadmap cut line](04_Project_Roadmap.md#7-cut-line-what-to-drop-first-if-behind). Mark them `` `CUT` `` and record them in the [change log](#change-log). **(M)** items are never cut.
5. `~Nh` is the time estimate at AI pair-programming pace. **[DEMO n]** marks a task that delivers demo item *n* ([roadmap §2](04_Project_Roadmap.md#2-demo-commitments-end-of-day-7)). Task IDs use the day number: `D1-05` is task 5 of Day 1.

**Legend:** `- [ ]` not started · `` `WIP` `` in progress · `- [x]` done · `` `BLOCKED` `` blocked · `` `CUT` `` descoped · **(M)** Must · **(S)** Should (on the cut line)

---

## Build approach: vertical slices

Work proceeds as thin end-to-end slices (alert in → agents → human decision), each tested before the next widens it. Tasks below keep their IDs and are ticked as slices complete them; the day sections remain the scope and the deadline.

| Slice | Scope | Status |
|---|---|---|
| 1 | Alert JSON → triage (BR-02 rules) → **Txn agent** (in-process tools) → **Narrative agent** → QA code checks (citations, BR-05 subset) → one rework → `human_review` interrupt → decision. In-memory checkpointer, JSON fixtures, CLI `sentinel run`. | **Done** — 25 offline tests + 2 live Bedrock E2E cases green (3/3 live runs) |
| 2 | **KYC + Screening agents**; parallel fan-out and join; specialist rework rejoins at narrative; 3rd fixture case (true sanctions match); two-phase agents (tool loop → structured output); **Streamlit test console** (`devtools/streamlit_app.py`, dev only) | **Done**: 32 offline tests + 4 live tests green (3 cases, 3/3 runs, plus a UI smoke test) |
| 3 | **Seeded generator** (305 customers, ~18k txns, 85 alerts with ground truth across 10 typologies) + **Postgres** (schema, loader, JSON/Postgres repositories); **4 MCP servers** in Docker (dev JWT, entity scoping, evidence as structured content); **OPA** deny-by-default middleware on every tool call; `check:` evidence for negative findings; **batch eval** (`sentinel eval`) | **Done**: 56 offline + 9 integration + 8 Rego tests; live suite green on the full stack; 20-case eval: escalation recall 1.0, false escalations 0.0, agreement 1.0, citation validity 1.0 |
| 4 | **Kafka** (KRaft + UI) with validated alert/decision/case-event schemas and a DLQ; **alert and decision workers** (idempotent start, stale-decision guard, retries → DLQ, case status in `cases.alerts`); **`AsyncPostgresSaver`**; one MCP session per agent run; `sentinel kafka …` / `sentinel worker` CLI | **Done**: 70 offline + 10 integration tests (incl. Kafka round trip); **Gate G3 passed**: 10 alerts via Kafka → review, all correct, 100% valid citations; decisions applied; duplicate alert and stale decision ignored |
| 5 | **Full squad**: Network agent (Neo4j + GDS, device-sharing mule rings), Typology & policy agent (versioned UK/ES manuals, Cohere Multilingual v3, hybrid pgvector search), independent LLM QA critic (Claude Haiku 4.5); lane routing; `graph_query` + `policy_kb` MCP servers; console Typology & Network tabs | **Done**: 87 offline + 17 integration tests; 24-case full-stack eval: escalation recall 1.0, false escalations 0.0, agreement 0.958 (acceptable 1.0), citation validity 1.0, 0 errors |
| 6 | **Guardrails**: PII redaction (Presidio + patterns, reversible tokens), injection rail, call limits and token budget, security event log; **UC-08** red-team probe through OPA; **UC-03** customer request drafted with a tipping-off rail and an approval step; **UC-04** customer reply → follow-up run on `{case_id}:rN` via `aml.case-followups.v1`; console Security tab, approval and reply forms, PII toggle | **Done**: 108 offline + 16 integration tests; **Gate G4 passed**: 28-case full-stack eval (the Slice 5 sample + 4 cases with planted injections): escalation recall 1.0, false escalations 0.0, agreement 0.964 (acceptable 1.0), citation validity 1.0, injections caught 4/4 with acceptable outcomes, 0 errors, ~85k tokens per case; red-team 6/6 denied; live UC-03 → UC-04 over Kafka |
| 7 | **Sentinel API** (FastAPI): queue, review packet with PII by role, checkpoint history, case events + SSE, decisions, approvals, customer replies, QA sampling/labels, health/readiness/metrics; **Amazon Cognito** sign-in (Terraform identity stack, 5 role groups, test users via `sentinel auth bootstrap`) with BR-08/09/10, UC-03 and UC-05 role rules; API container in Compose; console **API mode** with role switcher and QA review. Then **Airflow** (DAGs) | **API done**: 118 offline + 18 integration tests; live: alert via API → worker → SSE progress → L1 decision via API with a Cognito token. **Airflow done**: 4 DAGs load and run through the scheduler (lists, graph, policy re-embed live); golden set 74/20; worker resumes runs that stopped mid-way; QA degrades when the critic model is unavailable |
| 8+ | Workbench, evals tooling, observability, AWS | |

---

## Progress summary

| Day | Theme | Tasks | Done | % | Est. hours | Gate |
|---|---|---|---|---|---|---|
| Day 1 | Foundations, Kafka, Airflow, data | 17 | 8 | 47% | ~10.5 | G1 — |
| Day 2 | MCP tools, OPA, Kafka + Airflow DAGs | 13 | 8 | 62% | ~10.0 | G2 — |
| Day 3 | Core graph, HITL, Kafka workers | 15 | 12 | 80% | ~10.75 | G3 ✔ |
| Day 4 | Full squad, guardrails, extended HITL | 14 | 13 | 93% | ~9.75 | G4 ✔ |
| Day 5 | API & full workbench | 18 | 6 | 33% | ~12.0 | G5 — |
| Day 6 | Eval, observability, CI, AWS foundation | 14 | 0 | 0% | ~10.25 | G6 — |
| Day 7 | AWS deploy, hardening, demo | 12 | 0 | 0% | ~9.75 | G7 — |
| **Total** | | **103** | **47** | **46%** | **~73** | |

---

## Day 1 — Foundations, Kafka, Airflow & synthetic data

**Platform**
- [ ] **D1-01** `WIP` (M) Monorepo skeleton, `uv`, pins (TDD §23), pre-commit; push to the GitHub remote; protect `main` · ~0.75h — repo, pins, GitHub push, branch per slice done; pre-commit and branch protection pending
- [ ] **D1-02** `WIP` (M) `.env.example`, `config/models.yaml`, `config.py` (local vs AWS profiles) · ~0.5h — `.env.example` + `config.py` (env-driven model IDs, data/tool/OPA modes) done; AWS profile pending
- [x] **D1-03** (M) Compose: Kafka (KRaft) + Kafka UI, Postgres+pgvector, OPA [DEMO 1] · ~0.75h ✔ Day 2 (Slices 3–4)
- [x] **D1-04** (M) Compose: Airflow 3 `standalone` (LocalExecutor, metadata DB in Postgres, `ingestion/airflow/dags` mounted) [DEMO 3] · ~0.75h ✔ Day 5 (Slice 7): Airflow 3.3.2 image with Sentinel in its own venv (`docker/airflow.Dockerfile`), UI on :8088, metadata DB `airflow` in the same Postgres; ~1.2 GB RAM
- [ ] **D1-05** (M) Compose: OTel Collector, Tempo, Prometheus, Grafana · ~0.5h
- [x] **D1-06** (M) Local Neo4j: connectivity from host and containers (`host.docker.internal`), GDS plugin check, read-only dev user · ~0.5h ✔ Day 2 (Slice 5): Neo4j Desktop 2026.09 + GDS 2026.09; host (`neo4j://`) and containers (`bolt://host.docker.internal`); dedicated read-only user still pending
- [ ] **D1-07** (M) Neo4j MCP server in the AI pair-programming tool (read-only creds, **dev only**, never an agent tool) · ~0.25h
- [ ] **D1-08** `WIP` (M) Makefile: `up`, `down`, `seed`, `test`, `eval`, `demo`, `aws-plan`, `aws-up`, `aws-down` · ~0.25h — `make` is not on the Windows dev machine, so `seed`/`eval` are `sentinel data generate|load` and `sentinel eval` CLI commands; a thin Makefile wrapper for POSIX/CI is pending
- [x] **D1-09** (M) Verify Bedrock for Haiku 4.5 / Sonnet 5.5 / Opus 5.5; pin IDs; Anthropic fallback (Q1) · ~0.75h ✔ Day 1 — Sonnet/Opus 5.5 not available on the account; DeepSeek V3.2 pinned (see Q1)
- [ ] **D1-10** (M) LangSmith dev project + env vars (BR-14) · ~0.25h

**Synthetic data** (TDD §3.5)
- [x] **D1-11** (M) Generator: customers, accounts, expected activity, devices, benign transactions · ~1h ✔ Day 2 (Slice 5): devices for every customer, benign shared household devices
- [ ] **D1-12** `WIP` (M) Generator: planted STRUCT, PASSTHRU, HRJ, PROFILE, MULE_RING · ~1h — all done incl. device-sharing MULE_RING (6 rings, planted last so earlier data is unchanged); an explicit PROFILE typology is still pending
- [ ] **D1-13** `WIP` (M) Generator: sanctions/PEP (true + near-miss), adverse media and CRM notes (incl. injection payloads), registry · ~0.75h — lists, adverse media, CRM notes and injection payloads done (Slice 6: 3 CRM notes + 1 adverse-media article, separate seed); registry pending
- [x] **D1-14** (M) Generator: alerts with ground truth; golden set of 100 (20 held out) · ~0.75h ✔ Day 5 (Slice 7): 94 alerts with ground truth (91 generated + 3 fixtures) split 74 dev / 20 held out, typology-balanced (`evals/datasets/golden.json`, `sentinel data golden`)
- [x] **D1-15** (M) Postgres migration (TDD §3.3) + loader · ~0.75h ✔ Day 1 (Slice 3): `datagen/schema.sql` + `sentinel data load`; Postgres/JSON parity test
- [x] **D1-16** (M) Neo4j loader + `graph_scores.py` (GDS, with `networkx` fallback for AWS) · ~0.75h ✔ Day 2 (Slice 5): `sentinel data graph` (`graphdb.py`); GDS WCC + Louvain, rule-based mule score; parity test vs networkx
- [ ] **D1-17** (M) **Gate G1:** clean-clone `make up && make seed`; Kafka/Airflow/Grafana/Neo4j reachable; patterns visible; models respond · ~0.25h

---

## Day 2 — MCP tools, OPA, Kafka plumbing & Airflow DAGs

- [x] **D2-01** (M) Shared MCP lib: dev-token auth, `legal_entity` check, argument limits, evidence-ID formatter (TDD §8.1) · ~0.75h ✔ Day 1 (Slice 3): `mcp_servers/servers.py` (HS256 dev JWT, entity claim) + `tools/common.py` (record scoping, lookback ≤ 400)
- [ ] **D2-02** `WIP` (M) `case_mgmt`: alert, history, `save_draft_narrative`, `set_agent_status`, `draft_customer_info_request`, `attach_customer_reply` · ~1h — `get_case_history` done; write tools come with UC-03/04
- [ ] **D2-03** `WIP` (M) `kyc_profile`: profile, expected activity, accounts, CRM notes · ~0.5h — profile (incl. expected activity and accounts) + CRM notes done; separate tools not needed yet
- [ ] **D2-04** `WIP` (M) `txn_history`: transactions, velocity, structuring, pass-through, peer compare (FR-030–034) · ~1.5h — all but `peer_compare` done
- [ ] **D2-05** `WIP` (M) `screening`: sanctions/PEP fuzzy (rapidfuzz), adverse media, registry · ~1h — sanctions/PEP + adverse media done; registry pending
- [x] **D2-06** (M) `graph_query`: template Cypher only, ≤ 2 hops, ≤ 50 nodes (BR-03) · ~0.75h ✔ Day 2 (Slice 5)
- [x] **D2-07** (M) MCP servers in Compose · ~0.5h ✔ Day 1 (Slice 3): one image, four containers (ports 8101–8104)
- [x] **D2-08** (M) Tool unit tests (positive, negative, limits) · ~1h ✔ Day 1 (Slice 3): tool, MCP in-memory and MCP-over-HTTP integration tests
- [x] **D2-09** (M) OPA Rego + `data.agent_tools` + `opa test` (FR-120/121) · ~0.75h ✔ Day 1 (Slice 3): 8/8 Rego tests; Python allow-list kept equal to `data.json` by a test
- [x] **D2-10** (M) `mcp_clients.py` per-agent loading + allow-list; verify `mcp<2`, adapters 0.3.2 · ~0.5h ✔ Day 1 (Slice 3): `mcp` 1.30, adapters 0.3.2; evidence travels as MCP structured content
- [x] **D2-11** (M) Kafka: topics (`alerts`, `decisions`, `case-events`, `dlq`), schema validation, alert producer CLI; config-switchable PLAINTEXT / MSK IAM (FR-001) · ~0.75h ✔ Day 2 (Slice 4): MSK IAM path built but only exercised on AWS
- [x] **D2-12** (M) Airflow DAGs: `sanctions_refresh`, `graph_rebuild`, `alert_replay` (golden alerts → Kafka) [DEMO 1, 3] · ~1h ✔ Day 5 (Slice 7): DAGs call the `sentinel` CLI; alert_replay = select → reset + publish → wait for review → score (report to `evals.runs`)
- [ ] **D2-13** (M) **Gate G2:** tool + OPA tests green; DAGs run; alerts land on `aml.alerts.v1` · ~0.25h

---

## Day 3 — Core agent graph, HITL & Kafka workers

- [x] **D3-01** (M) `state.py` with reducers + tests (TDD §5.1) · ~0.5h ✔ Day 1 (Slice 1)
- [x] **D3-02** (M) Pydantic output schemas for all 8 agents · ~0.75h ✔ Day 2 (Slice 5)
- [ ] **D3-03** `WIP` (M) Agent factory + prompt loader + prompts v1 · ~1h — two-phase factory (tool loop → structured output), loader, prompts for 4 agents done; middleware hooks pending
- [ ] **D3-04** `WIP` (M) Triage: lane rules (BR-02) + LLM upgrade-only · ~0.75h — rules + tests done; LLM upgrade pending
- [x] **D3-05** (M) KYC context agent · ~0.5h ✔ Day 1 (Slice 2): in-process tools until D2-03
- [x] **D3-06** (M) Transaction analytics agent · ~0.5h ✔ Day 1 (Slice 1) — in-process detector tools until D2-04 moves them to MCP
- [x] **D3-07** (M) Screening agent · ~0.5h ✔ Day 1 (Slice 2): rapidfuzz sanctions/PEP + adverse media, in-process until D2-05
- [x] **D3-08** (M) Typology stub (rules-based recommendation) · ~0.25h ✔ Day 2: superseded by the full Typology & policy agent (D4-06)
- [ ] **D3-09** `WIP` (M) Narrative agent: claims with evidence IDs; model by lane · ~0.75h — cited claims done; model-by-lane pending
- [x] **D3-10** (M) Citation guard + QA critic (model ≠ narrative) + `rework` node and routing · ~1.25h ✔ Day 2 (Slice 5): Claude Haiku 4.5 critic after the code checks; rework only for blocker/major
- [x] **D3-11** (M) `graph.py` wiring + tests: fan-out/join, fast vs full, two QA failures → human · ~1h ✔ Day 2 (Slice 5)
- [x] **D3-12** (M) `AsyncPostgresSaver` + `human_review` interrupt + OPA `@wrap_tool_call` middleware · ~0.75h ✔ Day 2 (Slices 3–4): checkpoint tables in schema `sentinel`
- [x] **D3-13** (M) Alert worker: idempotent start (FR-002), DLQ, events to `aml.case-events.v1` (TDD §13) · ~1h ✔ Day 2 (Slice 4): crash-recovery scan of `in_progress` cases still pending
- [x] **D3-14** (M) Decision worker: consume `aml.decisions.v1`, check `snapshot.next`, resume (TDD §15) · ~0.75h ✔ Day 2 (Slice 4)
- [x] **D3-15** (M) **Gate G3:** 10 alerts via Kafka → `awaiting_review` with 100% valid citations; a Kafka decision completes the run · ~0.5h ✔ Day 2: 10/10 reached review, 10/10 correct, 0 invalid citations; 10 decisions applied

---

## Day 4 — Full squad, guardrails & extended HITL

- [x] **D4-01** (M) Synthetic policy manuals (UK, ES) + typology guide, versioned · ~0.75h ✔ Day 2 (Slice 5): `data/policies/` (AML-UK v3.2 English, AML-ES v2.1 Spanish, TYP-GUIDE v1.4)
- [x] **D4-02** (S) Docling parse + HybridChunker + embeddings → `kb.policy_chunks` (Q2) · ~1h ✔ Day 2 (Slice 5): section chunker for Markdown manuals (Docling only needed for PDF/Word) + Cohere Multilingual v3
- [x] **D4-03** (M) `policy_kb` server: hybrid search (vector + full-text, RRF), entity filter · ~0.75h ✔ Day 2 (Slice 5): falls back to full text if embedding fails; cross-language retrieval verified
- [x] **D4-04** (M) Airflow DAG `policy_reembed` [DEMO 3] · ~0.25h ✔ Day 5 (Slice 7)
- [x] **D4-05** (M) Network agent, full lane only (FR-050–053) · ~0.75h ✔ Day 2 (Slice 5)
- [x] **D4-06** (M) Typology & policy agent replacing the stub (FR-060–063) · ~0.75h ✔ Day 2 (Slice 5): policy sections pre-retrieved in code so every recommendation cites real policy IDs
- [x] **D4-07** (M) Presidio PII middleware; custom recognisers are (S) (FR-122) · ~0.75h ✔ Day 4 (Slice 6): Presidio analyzer container (en_core_web_lg) for names + pattern recognisers (IBAN, sort code, email, phone) + exact customer name/DOB; reversible HMAC tokens, restored for tools and the console
- [x] **D4-08** (M) Injection rail on tool outputs (FR-123, lightweight) · ~0.75h ✔ Day 4 (Slice 6): regex rail fences instruction-like tool output as untrusted data; curated policy manuals exempt
- [x] **D4-09** (M) Budgets: tool-call limit, token budget, recursion limit (FR-125) · ~0.5h ✔ Day 4 (Slice 6): per-agent model/tool-call limits (middleware), per-case token budget stops rework and request-info
- [x] **D4-10** (M) UC-08: injected "close this alert" → OPA deny, logged and counted [DEMO 7] · ~0.5h ✔ Day 4 (Slice 6): `sentinel redteam` + console Security tab; 6/6 forged calls denied by the live OPA
- [x] **D4-11** (M) UC-03: `HumanInTheLoopMiddleware` on `draft_customer_info_request` + tipping-off check (BR-11) [DEMO 4] · ~1h ✔ Day 4 (Slice 6): implemented as a main-graph step (`draft_info_request` → `approve_info_request` interrupt) instead of tool middleware; approve/edit/reject via console or Kafka
- [x] **D4-12** (M) UC-04: `request_info` → customer reply → follow-up run on `{case_id}:r1` (FR-096, TDD §7.2) [DEMO 5] · ~1h ✔ Day 4 (Slice 6): replies on `aml.case-followups.v1`; reply + previous review as evidence; `cases.customer_replies`
- [ ] **D4-13** `WIP` (M) Baseline golden run: 100 cases via `alert_replay` → Kafka; record recall/agreement/completeness · ~0.75h — pipeline ready (`alert_replay` / `sentinel replay run --split all`); full 94-case run pending (Bedrock cost and the QA critic model outage)
- [x] **D4-14** (M) **Gate G4:** 8 agents; BR-05 checklist passes; UC-03/04/08 work via CLI/API; baseline logged · ~0.25h

---

## Day 5 — API & full workbench

**API**
- [x] **D5-01** (M) FastAPI skeleton: config, `/healthz`, `/metrics` · ~0.25h ✔ Day 5 (Slice 7): plus `/readyz` (Postgres + Kafka) and `/me`; API image (`docker/sentinel.Dockerfile`) in Compose
- [x] **D5-02** (M) `POST /cases` (→ Kafka), `GET /cases`, `GET /cases/{id}`, `GET /cases/{id}/history` · ~0.75h ✔ Day 5 (Slice 7): queue filters status/lane/entity; review packet restores PII only for l1/l2/admin and masks raw evidence for others (FR-109)
- [x] **D5-03** (M) `POST /cases/{id}/decision` → `aml.decisions.v1`; roles `l1`/`l2`/`qa`/`sme`; RBAC (BR-08/09/10) · ~0.75h ✔ Day 5 (Slice 7): Amazon Cognito user pool (groups l1, l2, qa, sme, admin) instead of a mock IdP; investigator ID from the token
- [x] **D5-04** (M) Approvals endpoint + customer-reply endpoint · ~0.5h ✔ Day 5 (Slice 7): `POST /cases/{id}/approval` (L2, tipping-off rail on edits) and `/reply`
- [x] **D5-05** (S) SSE stream from `aml.case-events.v1` (FR-102) · ~0.75h ✔ Day 5 (Slice 7): `/cases/{id}/stream` (history then live) + `/cases/{id}/events`
- [x] **D5-06** (M) QA sampling + labels endpoint (UC-05) · ~0.5h ✔ Day 5 (Slice 7): rubric of four 1–5 scores + decision-correct flag in `cases.qa_labels`

**Workbench** [DEMO 6]
- [ ] **D5-07** (M) React + TS + Vite scaffold, role switcher, shared layout · ~0.5h
- [ ] **D5-08** (M) Queue view with status/lane/entity filters (FR-100) · ~0.75h
- [ ] **D5-09** (M) Case view: overview, evidence pack, findings, narrative with evidence chips (FR-101) [DEMO 2] · ~1.5h
- [ ] **D5-10** (M) Decision panel: action, reason code, agreement flag, edits (FR-091/092) [DEMO 2] · ~0.75h
- [ ] **D5-11** (M) Live progress panel (SSE, or 2 s polling fallback) [DEMO 1] · ~0.5h
- [ ] **D5-12** (M) Network graph view, Cytoscape.js (FR-104) · ~1h
- [ ] **D5-13** (M) Audit timeline: checkpoint history + Tempo trace link (FR-105, UC-07) · ~0.75h
- [ ] **D5-14** (M) Tool-approval dialog + request-info / customer-reply screen (FR-108, UC-03/04) [DEMO 4, 5] · ~0.75h
- [ ] **D5-15** (M) QA labelling screen with rubric (FR-106) · ~0.5h
- [ ] **D5-16** (S) Blind mode (FR-107) · ~0.25h
- [ ] **D5-17** (M) Playwright E2E: decide, approve, request-info flows · ~0.75h
- [ ] **D5-18** (M) **Gate G5:** demo items 1, 2, 4, 5, 6 work end to end in the browser (local) · ~0.25h

---

## Day 6 — Evaluation, observability, CI & AWS foundation

**Evaluation**
- [ ] **D6-01** (M) DeepEval per-agent structured-output tests · ~0.5h
- [ ] **D6-02** (M) DeepEval golden suite: recall, agreement, completeness, citations + thresholds (FR-141) · ~1h
- [ ] **D6-03** (S) G-Eval narrative rubric · ~0.5h
- [ ] **D6-04** (M) Promptfoo red-team, ~15 attacks (FR-143) · ~1h
- [ ] **D6-05** (S) LangSmith experiment, 30-case slice vs baseline (FR-142) · ~0.75h
- [ ] **D6-06** (S) Airflow `nightly_eval` DAG: sample checkpoints, judge, scores → Postgres [DEMO 3] · ~0.5h

**Observability & CI**
- [ ] **D6-07** (M) OTel-only tracing → Tempo, span attributes; nothing goes to LangSmith (FR-150, BR-14) [DEMO 9] · ~0.75h
- [ ] **D6-08** (M) Prometheus metrics + Grafana platform, KPI and security dashboards (FR-151/152) [DEMO 7, 9] · ~1h
- [ ] **D6-09** (M) `ci.yml` (lint, types, unit, `opa test`) + `eval-gate.yml` (DeepEval held-out slice) · ~0.75h
- [ ] **D6-10** (M) Degraded-prompt PR blocked by the eval gate [DEMO 8] · ~0.25h

**AWS foundation** (TDD §19.3)
- [ ] **D6-11** (M) Production Dockerfiles for all services; CI builds and pushes images to ECR · ~0.75h
- [ ] **D6-12** (M) Terraform: network (VPC, NAT, VPC endpoints), ECR, Secrets Manager, IAM (task roles, GitHub OIDC deploy role); check service quotas · ~1.25h
- [ ] **D6-13** (M) Terraform: Aurora Serverless v2 (pgvector) + MSK Serverless; **apply**; prototype the MSK IAM token provider · ~1h
- [ ] **D6-14** (M) **Gate G6:** `make eval` passes; regression PR blocked; dashboards live; Aurora/MSK/ECR applied · ~0.25h

---

## Day 7 — AWS deploy, hardening & demo

**AWS** [DEMO 10]
- [ ] **D7-01** (M) Terraform ECS: cluster, Cloud Map, services (api, worker + OPA sidecar, 6 MCP, airflow, workbench, observability), ALB with IP allow-list · ~1.5h
- [ ] **D7-02** (M) MSK IAM auth live, topic-init task; Neo4j AWS target per Q7 (`networkx` scores); migration + seed task · ~0.75h
- [ ] **D7-03** (M) `deploy.yml`: GitHub OIDC → ECR → ECS service update · ~0.75h
- [ ] **D7-04** (M) AWS smoke test: `alert_replay` → case → approval → decision in the workbench. **mid-day contingency checkpoint** (roadmap §5) · ~0.75h

**Hardening**
- [ ] **D7-05** (M) Crash test: stop the worker task mid-case → resumes from checkpoint (NFR-03) · ~0.5h
- [ ] **D7-06** (M) 100-alert load run: p50/p95 by lane; cost per case (NFR-01, NFR-11) · ~0.75h
- [ ] **D7-07** (M) Security pass: secrets scan, OPA coverage, PII-in-traces, ALB allow-list · ~0.5h

**Delivery**
- [ ] **D7-08** (M) Eval and KPI report vs targets (HLD §2.4) · ~0.5h
- [ ] **D7-09** (M) README (local + AWS quickstart) + demo script covering demo items 1–10 · ~0.75h
- [ ] **D7-10** (M) Recorded walkthrough · ~0.75h
- [ ] **D7-11** (M) Update HLD/TDD/Functional Spec to as-built (roadmap §8) · ~1h
- [ ] **D7-12** (M) **Gate G7:** demo delivered; tag `v1.0`; `terraform destroy` scheduled after the demo · ~0.25h

---

## Backlog after Day 7 (not counted)

- [ ] **B-01** NeMo Guardrails input/output rails; Ragas; Cohere rerank
- [ ] **B-02** Lessons memory (FR-110–113, UC-06)
- [ ] **B-03** Debezium CDC; Iceberg lake
- [ ] **B-04** EKS + Helm + KEDA + Argo CD; MWAA; Network Firewall; WORM archive
- [ ] **B-05** Image signing + SBOM
- [ ] **B-06** A2A cross-entity check (UC-10)
- [ ] **B-07** Golden set to 300–500 cases; LangSmith slice to ~200

---

## Open questions

| # | Question | Needed by | Status | Answer |
|---|---|---|---|---|
| Q1 | Bedrock inference profile IDs for Opus 5.5 / Sonnet 5.5 / Haiku 4.5 (or the Anthropic API?) | D1-09 | **Answered** | Sonnet/Opus 5.5 return "not available for this account". Use `deepseek.v3.2` (eu-west-2, on-demand; text + tool calls verified) for all agents; `deepseek.v3-v1:0` and `eu.anthropic.claude-haiku-4-5-20251001-v1:0` also work (Haiku is the candidate QA-critic model, ≠ narrative). Set via `SENTINEL_MODEL_*` |
| Q2 | Embedding model: Cohere on Bedrock or Titan? | D4-02 | **Answered** | Cohere Multilingual v3 (1024 dims; English + Spanish policies); Titan Text v2 also available |
| Q3 | Neo4j locally | D1-06 | **Answered** | Neo4j Desktop 2026.09 Enterprise with GDS 2026.09 |
| Q4 | GitHub remote for Actions | D1-01 | **Answered** | Yes |
| Q5 | Kafka / Airflow / AWS / extended HITL / extra screens in scope? | — | **Answered** | Yes, all needed for the demo |
| Q6 | AWS target | D6-12 | **Answered** | Managed-lite (TDD §19.3) |
| Q7 | Neo4j on AWS: AuraDB Free (recommended) or Neo4j on ECS + EFS? | D6-12 | Open | |
| Q8 | AWS account, region (`eu-west-1`?) and demo budget confirmed? | D6-12 | Partly answered | Account in use with `aws login`, region `eu-west-2`; demo budget still open |

---

## Blockers & issues

| Day | Task | Description | Resolution | Closed (day) |
|---|---|---|---|---|
| Day 1 | D1-09 | Claude Sonnet/Opus 5.5 not available on the AWS account | Switched to DeepSeek V3.2 on Bedrock (Q1) | Day 1 |
| Day 1 | D1-09 | boto3 could not read `aws login` credentials | Added `botocore[crt]` to requirements | Day 1 |
| Day 1 | Slice 1 | deepeval's auto-loaded pytest plugin imports `langchain.schema` (removed in LangChain 1.x) and breaks every test run | Disabled with `-p no:plugins` in `pyproject.toml`; revisit when deepeval evals are wired (D6) | Day 1 |
| Day 1 | Slice 2 | Screening agent looped until the recursion limit: `create_agent(response_format=ToolStrategy)` forces `tool_choice="any"` every turn, and DeepSeek re-calls the data tools instead of the output tool | Agents now run in two phases: tool loop with `tool_choice=auto`, then one `with_structured_output` call (`agents/factory.py`) | Day 1 |
| Day 1 | Slice 3 | First 20-case eval: citation validity 0.75. Every unknown citation was a negative finding ("no sanctions hits", "no CRM notes"): tools returned no evidence when a check found nothing | Tools now return a `check:<kind>:<subject>` evidence item for empty results (names hashed, no PII in IDs); narrative prompt v1.3. Validity 1.0 | Day 1 |
| Day 1 | Slice 3 | Benign cash businesses escalated: generator planted the large deposits on top of normal takings, so cash ran 2–3× the declared profile (ground truth was wrong, not the model) | Planted deposits now replace ordinary takings, on businesses large enough to absorb them | Day 1 |
| Day 1 | Slice 3 | OPA `ConnectTimeout` under load: each new connection through Docker Desktop on Windows costs ~0.55 s and queues (up to 22 s) | One pooled `httpx` client per event loop + one retry (still fails closed). Same per-call connection cost slows MCP (new session per tool call): one session per server per agent run added in Slice 4 | Day 1 (OPA), Day 2 (MCP) |
| Day 2 | Slice 4 | psycopg's async driver (Postgres checkpointer) cannot run on Windows' default Proactor event loop | CLI and tests use the selector loop on Windows; Linux containers on AWS are unaffected | Day 2 |
| Day 2 | Slice 5 | Typology agent cited invented policy IDs (citation validity 0 on a 5-case run): it often skipped its search tool and reconstructed plausible references | Policy sections relevant to the findings are retrieved in code and put in the brief; the final structured-output step is given the exact IDs to copy (all agents). Validity 1.0 | Day 2 |
| Day 2 | Slice 5 | GDS 2026.09 rejects `randomSeed` for Louvain; Neo4j `devices_of` dropped devices nobody else uses (one pattern cannot reuse a relationship) | Removed the option; users matched in a second MATCH; Neo4j/networkx parity test | Day 2 |
| Day 3 | Slice 5 | OPA kept the old allow-list after `data.json` changed (only read at start), so the network agent's tools were denied in one eval case | OPA runs with `--watch`; integration test compares the running engine's allow-list with the code | Day 3 |
| Day 3 | Slice 5 | `aws login` session expired during a long eval while the laptop slept: 14 of 24 cases errored | Re-login and rerun; long runs need an awake machine (on AWS the task role refreshes credentials) | Day 3 |
| Day 4 | D4-07 | Windows Application Control blocks the spaCy model DLLs on the dev machine, so Presidio cannot run in-process | Presidio analyzer runs as a Docker service (en_core_web_lg) called over HTTP; pattern and exact-name redaction still apply if it is down. Presidio misses some Spanish names, which the exact customer-name match covers | Day 4 |
| Day 4 | D4-08 | First G4 eval: the injection rail fired on the UK procedure manual ("close the alert") | Curated policy-manual search is exempt from the rail; record-derived text (CRM notes, media, payment references) is still scanned | Day 4 |
| Day 4 | D4-07 | Second G4 eval: citation validity 0.0 although every outcome was acceptable. The email pattern matched versioned policy IDs (`AML-UK@3.2`), so models cited tokens instead of real IDs | Email pattern requires a letter TLD; regression test keeps policy IDs intact. Two transient MCP task-group errors under load now report their inner exception | Day 4 |
| Day 4 | D4-07, D4-09 | Same run: one structuring case got `request_info`, one case hit the agent recursion limit (25). Presidio's English model tagged reason codes (`STRUCTURING_CONFIRMED`) and Spanish policy phrases as people, so models saw tokens instead of codes; the recursion limit was lower than the new call limits allow | Presidio spans with underscores, IDs or lower-case words are ignored (policy manuals now produce no PERSON hits); recursion limit raised to 60 as a backstop behind the call limits | Day 4 |

---

| Day 5 | D2-12, D4-13 | First `alert_replay` run: Bedrock returned `ServiceUnavailableException` for the QA critic (Claude Haiku 4.5, EU profile) on every retry. The worker's retry then saw the saved checkpoint, treated the alert as a duplicate and left 3 cases `in_progress` for good; the DAG failed at `wait_for_review` | Worker resumes a run that stopped mid-way from its last checkpoint (`case_resumed`) instead of ignoring it (also covers NFR-03 crash resume); QA degrades to code checks with a visible "critic unavailable" note when the critic model fails after its retries | Day 5 (fix); outage on AWS side |

---

## Gate log

| Gate | Day | Result (Go / No-go) | Key metrics | Notes |
|---|---|---|---|---|
| G1 Platform & data ready | Day 1 | | | |
| G2 Tools & pipelines ready | Day 2 | | | |
| G3 Streaming E2E MVP | Day 3 | **Go** (met on Day 2) | 10/10 cases reached review via Kafka; 100% valid citations; 10/10 recommendations correct; 10/10 decisions applied | Full stack (Postgres, MCP, OPA). Remaining Day 3 items (LLM lane upgrade, typology stub, LLM QA critic, model by lane) do not block the gate |
| G4 Full squad + HITL flows | Day 4 | **Go** | 28 cases: recall 1.0, false escalations 0.0, agreement 0.964, acceptable 1.0, citation validity 1.0, injections caught 4/4, 0 errors | UC-03/04 live via Kafka CLI, UC-08 via `sentinel redteam` (API comes on Day 5). Two earlier runs were reworked after guardrail false positives (see Blockers) |
| G5 Complete workbench | Day 5 | | E2E pass | |
| G6 Gated release + AWS base | Day 6 | | eval scores, red-team pass rate | |
| G7 Demo on AWS (v1.0) | Day 7 | | p95 latency, cost/case | |

---

## Daily log

| Day | Done | Carried over | Notes |
|---|---|---|---|
| Day 1 | Slice 1 end to end (D1-09, D3-01, D3-06; D3-02/03/04/09/10/11/12 started) | Remaining Day 1 platform tasks | Live: structuring case → escalate with 100% valid citations; benign case → not escalated after prompt v1.1 calibration. Slice 2: KYC + Screening in parallel (D3-05, D3-07); sanctions true match → escalate SANCTIONS_TRUE_MATCH, near-miss names discounted; Streamlit test console added. Slice 3: generator, Postgres, MCP servers, OPA (D1-15, D2-01, D2-07–D2-10); 20-case eval on the full stack all correct with valid citations |
| Day 2 | Slice 4: Kafka + UI, workers, Postgres checkpointer, persistent MCP sessions (D1-03, D2-11, D3-12–D3-15); Gate G3 passed | Airflow, Neo4j, observability, LangSmith; pre-commit/branch protection | One case ~49 s alone on the full stack (MCP overhead gone); ~75–125 s each with 5 in parallel, limited by Bedrock throughput |
| Day 3 | Slice 5: full squad (D1-06, D1-11, D1-16, D2-06, D3-02, D3-08, D3-10, D3-11, D4-01–D4-03, D4-05, D4-06); 24-case baseline recorded | Guardrails, UC-03/04/08, Airflow | Mean ~134 s per case with 4 in parallel (two more agents + critic) |
| Day 4 | Slice 6: guardrails and extended HITL (D4-07–D4-12, D1-13 payloads); Gate G4 | Airflow, API, workbench | Live: redteam 6/6 denied by OPA; CASE-G0039 drafted request (rail passed) → approved → request_info → customer reply → follow-up `:r1` cites the reply and escalates; mean ~160 s per case with 4 in parallel |
| Day 5 | Slice 7: Sentinel API with Cognito sign-in (D5-01–D5-06) | Airflow DAGs, workbench | Cognito created with Terraform (first AWS resource beyond Bedrock); live API flow verified with real tokens |
| Day 6 | | | |
| Day 7 | | | |

---

## Change log

| Version / Day | Change | Reason | Impact |
|---|---|---|---|
| v0.1 · planning | Initial long-form task plan (125 tasks) | Project kick-off | — |
| v0.2 · planning | Re-planned to 7 days (85 tasks) | 7-day deadline | Kafka, Airflow, AWS, UC-03/04 and several screens moved to backlog |
| v0.3 · planning | Restored Kafka, Airflow, AWS (managed-lite), UC-03/04 and the workbench screens as demo commitments (103 tasks); local Neo4j; GitHub remote confirmed | Needed for the Day 7 demo | Denser days (~10–12 h); cut line limited to (S) items; AWS foundation applied on Day 6 |
| v0.4 · planning | Removed calendar dates; plan expressed as Day 1–Day 7 only | Plan must be generic | No scope change |
| v0.5 · Day 1 | Build in vertical slices (Slice 1 = 2 agents end to end) instead of layer by layer; models switched from Claude Sonnet/Opus 5.5 to DeepSeek V3.2 on Bedrock | Owner's choice to start small and scale; Claude 5.5 models unavailable on the account | No scope change; task order changes. TDD §6.2 model column and HLD to be updated to as-built (D7-11) |
| v0.6 · Day 1 | Added a Streamlit test console (`devtools/`) as a developer tool alongside the React workbench | Owner wants a simple way to exercise the flow before the API and workbench exist | Dev only, not deployed; React workbench (Day 5) unchanged. Once the API exists the console can call it instead of importing the graph |
| v0.7 · Day 1 | Slice 3 as-built deviations: one MCP image serving four servers (`python -m mcp_servers <name>`) instead of a package per server; `kyc` agent also gets `case_mgmt.get_case_history`; `make` replaced by `sentinel data|eval` CLI commands on Windows; new evidence type `check:<kind>:<subject>` for negative findings; dev tokens are HS256 shared-secret JWTs | Simpler build; Windows dev machine; citations for "nothing found" statements | TDD §3.3/§8 and Functional Spec evidence-ID list to be updated as-built (D7-11). Eval sample (20 of 88) was also used for diagnosis, so a held-out run is still needed (D1-14) |
| v0.8 · Day 2 | Slice 4 as-built: workers run as host processes (`sentinel worker`) rather than containers until the AWS slice; one DLQ topic receives failures from both alert and decision consumers (header `source_topic`); case status kept in `cases.alerts.status` | Workers need the developer's Bedrock credentials locally; on AWS the task role provides them | Containerise workers with D6/D7 AWS tasks |
| v0.9 · Day 3 | Slice 5 as-built: Neo4j runs in Neo4j Desktop on the host (containers reach it via `host.docker.internal`); policy manuals are Markdown chunked by section (Docling kept for PDF/Word); the typology node pre-retrieves policy sections in code; the QA critic runs only after the code checks pass and minor issues never trigger rework; triage adds an `alert:<case>` evidence item; data/graph/kb loading via `sentinel data load|kb|graph` | Reliability of citations; cost (no critic call on drafts that already fail code checks) | TDD §4, §6.2–6.3 and the Functional Spec evidence-ID list to be updated as-built (D7-11) |
| v1.0 · Day 4 | Slice 6 as-built: UC-03 approval is a main-graph step (`draft_info_request` → `approve_info_request` interrupt) rather than `HumanInTheLoopMiddleware` on a tool; customer replies get their own topic `aml.case-followups.v1`; Presidio runs as a Docker service; PII tokens are reversible HMAC tokens kept in case state | Approval is checkpointed and resumable over Kafka like the decision, visible in the graph for auditors, and no agent holds a tool that contacts the customer | Rationale for UC-03 to be written up in the demo document; TDD §7 and §10 to be updated as-built (D7-11) |
| v1.1 · Day 5 | Slice 7 as-built: API sign-in uses Amazon Cognito from the start (Terraform `infra/terraform/identity`, kept apart from the hourly-billed platform stack) instead of a local mock IdP, with an HS256 dev mode for offline tests and CI; roles l1, l2, qa, sme, admin as Cognito groups; approvals at `POST /cases/{id}/approval` (one pending interrupt per case, no interrupt ID); added `/cases/{id}/reply`, `/events`, `/me`, `/readyz` and QA endpoints; lane recorded in `cases.alerts.tier`; API container in Compose while the workers stay on the host | AWS deployment is close and Cognito is free at demo scale with nothing running; the workbench needs a real sign-in page (hosted login) | Aurora, MSK and ECS stay local until the AWS slice (hourly cost). TDD §12 to be updated as-built (D7-11). Deployed workbench needs an HTTPS callback URL (CloudFront or ACM certificate) |
| v1.2 · Day 5 | Slice 7 Airflow as-built: one custom Airflow 3.3.2 image with Sentinel in a separate virtual environment; DAG tasks are `BashOperator`s calling the `sentinel` CLI (no Sentinel imports in Airflow's own environment); `alert_replay` scores from the Postgres checkpointer after the workers run (reports in `evals.runs`); golden set is 94 cases (74 dev / 20 held out) rather than 100; local Airflow has no login (`SIMPLE_AUTH_MANAGER_ALL_ADMINS`) | Avoids dependency clashes with Airflow's pins; the same commands run from a terminal; all alerts with ground truth are used | On AWS: Airflow on ECS (or MWAA in the backlog) with login; `nightly_eval` (D6-06) reuses `sentinel replay` |
