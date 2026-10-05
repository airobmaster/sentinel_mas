# Sentinel — Project Task Plan (Living Checklist, 1-Week Build)

| Item | Value |
|---|---|
| Document | 05 — Project Task Plan |
| Version | 0.5 (vertical slices; DeepSeek on Bedrock) |
| Owner | Rakesh Velayudhan, AI pair-programming |
| Window | 7 days: Day 1 → Day 7 (demo at the end of Day 7) |
| Current day | Day 1: Slices 1–2 done (see [Build approach](#build-approach-vertical-slices)) |
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
| 3 | Synthetic generator + Postgres; tools behind MCP; OPA | |
| 4 | Kafka alert/decision workers; Postgres checkpointer | |
| 5+ | Network, typology, LLM QA critic, guardrails, extended HITL, API, workbench, evals, AWS | |

---

## Progress summary

| Day | Theme | Tasks | Done | % | Est. hours | Gate |
|---|---|---|---|---|---|---|
| Day 1 | Foundations, Kafka, Airflow, data | 17 | 1 | 6% | ~10.5 | G1 — |
| Day 2 | MCP tools, OPA, Kafka + Airflow DAGs | 13 | 0 | 0% | ~10.0 | G2 — |
| Day 3 | Core graph, HITL, Kafka workers | 15 | 4 | 27% | ~10.75 | G3 — |
| Day 4 | Full squad, guardrails, extended HITL | 14 | 0 | 0% | ~9.75 | G4 — |
| Day 5 | API & full workbench | 18 | 0 | 0% | ~12.0 | G5 — |
| Day 6 | Eval, observability, CI, AWS foundation | 14 | 0 | 0% | ~10.25 | G6 — |
| Day 7 | AWS deploy, hardening, demo | 12 | 0 | 0% | ~9.75 | G7 — |
| **Total** | | **103** | **5** | **5%** | **~73** | |

---

## Day 1 — Foundations, Kafka, Airflow & synthetic data

**Platform**
- [ ] **D1-01** (M) Monorepo skeleton, `uv`, pins (TDD §23), pre-commit; push to the GitHub remote; protect `main` · ~0.75h
- [ ] **D1-02** (M) `.env.example`, `config/models.yaml`, `config.py` (local vs AWS profiles) · ~0.5h
- [ ] **D1-03** (M) Compose: Kafka (KRaft) + Kafka UI, Postgres+pgvector, OPA [DEMO 1] · ~0.75h
- [ ] **D1-04** (M) Compose: Airflow 3 `standalone` (LocalExecutor, metadata DB in Postgres, `ingestion/airflow/dags` mounted) [DEMO 3] · ~0.75h
- [ ] **D1-05** (M) Compose: OTel Collector, Tempo, Prometheus, Grafana · ~0.5h
- [ ] **D1-06** (M) Local Neo4j: connectivity from host and containers (`host.docker.internal`), GDS plugin check, read-only dev user · ~0.5h
- [ ] **D1-07** (M) Neo4j MCP server in the AI pair-programming tool (read-only creds, **dev only**, never an agent tool) · ~0.25h
- [ ] **D1-08** (M) Makefile: `up`, `down`, `seed`, `test`, `eval`, `demo`, `aws-plan`, `aws-up`, `aws-down` · ~0.25h
- [x] **D1-09** (M) Verify Bedrock for Haiku 4.5 / Sonnet 5.5 / Opus 5.5; pin IDs; Anthropic fallback (Q1) · ~0.75h ✔ Day 1 — Sonnet/Opus 5.5 not available on the account; DeepSeek V3.2 pinned (see Q1)
- [ ] **D1-10** (M) LangSmith dev project + env vars (BR-14) · ~0.25h

**Synthetic data** (TDD §3.5)
- [ ] **D1-11** (M) Generator: customers, accounts, expected activity, devices, benign transactions · ~1h
- [ ] **D1-12** (M) Generator: planted STRUCT, PASSTHRU, HRJ, PROFILE, MULE_RING · ~1h
- [ ] **D1-13** (M) Generator: sanctions/PEP (true + near-miss), adverse media and CRM notes (incl. injection payloads), registry · ~0.75h
- [ ] **D1-14** (M) Generator: alerts with ground truth; golden set of 100 (20 held out) · ~0.75h
- [ ] **D1-15** (M) Postgres migration (TDD §3.3) + loader · ~0.75h
- [ ] **D1-16** (M) Neo4j loader + `graph_scores.py` (GDS, with `networkx` fallback for AWS) · ~0.75h
- [ ] **D1-17** (M) **Gate G1:** clean-clone `make up && make seed`; Kafka/Airflow/Grafana/Neo4j reachable; patterns visible; models respond · ~0.25h

---

## Day 2 — MCP tools, OPA, Kafka plumbing & Airflow DAGs

- [ ] **D2-01** (M) Shared MCP lib: dev-token auth, `legal_entity` check, argument limits, evidence-ID formatter (TDD §8.1) · ~0.75h
- [ ] **D2-02** (M) `case_mgmt`: alert, history, `save_draft_narrative`, `set_agent_status`, `draft_customer_info_request`, `attach_customer_reply` · ~1h
- [ ] **D2-03** (M) `kyc_profile`: profile, expected activity, accounts, CRM notes · ~0.5h
- [ ] **D2-04** (M) `txn_history`: transactions, velocity, structuring, pass-through, peer compare (FR-030–034) · ~1.5h
- [ ] **D2-05** (M) `screening`: sanctions/PEP fuzzy (rapidfuzz), adverse media, registry · ~1h
- [ ] **D2-06** (M) `graph_query`: template Cypher only, ≤ 2 hops, ≤ 50 nodes (BR-03) · ~0.75h
- [ ] **D2-07** (M) MCP servers in Compose · ~0.5h
- [ ] **D2-08** (M) Tool unit tests (positive, negative, limits) · ~1h
- [ ] **D2-09** (M) OPA Rego + `data.agent_tools` + `opa test` (FR-120/121) · ~0.75h
- [ ] **D2-10** (M) `mcp_clients.py` per-agent loading + allow-list; verify `mcp<2`, adapters 0.3.2 · ~0.5h
- [ ] **D2-11** (M) Kafka: topics (`alerts`, `decisions`, `case-events`, `dlq`), schema validation, alert producer CLI; config-switchable PLAINTEXT / MSK IAM (FR-001) · ~0.75h
- [ ] **D2-12** (M) Airflow DAGs: `sanctions_refresh`, `graph_rebuild`, `alert_replay` (golden alerts → Kafka) [DEMO 1, 3] · ~1h
- [ ] **D2-13** (M) **Gate G2:** tool + OPA tests green; DAGs run; alerts land on `aml.alerts.v1` · ~0.25h

---

## Day 3 — Core agent graph, HITL & Kafka workers

- [x] **D3-01** (M) `state.py` with reducers + tests (TDD §5.1) · ~0.5h ✔ Day 1 (Slice 1)
- [ ] **D3-02** `WIP` (M) Pydantic output schemas for all 8 agents · ~0.75h — kyc, txn, screening, narrative done
- [ ] **D3-03** `WIP` (M) Agent factory + prompt loader + prompts v1 · ~1h — two-phase factory (tool loop → structured output), loader, prompts for 4 agents done; middleware hooks pending
- [ ] **D3-04** `WIP` (M) Triage: lane rules (BR-02) + LLM upgrade-only · ~0.75h — rules + tests done; LLM upgrade pending
- [x] **D3-05** (M) KYC context agent · ~0.5h ✔ Day 1 (Slice 2): in-process tools until D2-03
- [x] **D3-06** (M) Transaction analytics agent · ~0.5h ✔ Day 1 (Slice 1) — in-process detector tools until D2-04 moves them to MCP
- [x] **D3-07** (M) Screening agent · ~0.5h ✔ Day 1 (Slice 2): rapidfuzz sanctions/PEP + adverse media, in-process until D2-05
- [ ] **D3-08** (M) Typology stub (rules-based recommendation) · ~0.25h
- [ ] **D3-09** `WIP` (M) Narrative agent: claims with evidence IDs; model by lane · ~0.75h — cited claims done; model-by-lane pending
- [ ] **D3-10** `WIP` (M) Citation guard + QA critic (model ≠ narrative) + `rework` node and routing · ~1.25h — citation guard, rework node, routing done; LLM critic pending
- [ ] **D3-11** `WIP` (M) `graph.py` wiring + tests: fan-out/join, fast vs full, two QA failures → human · ~1h — fan-out/join, specialist + narrative rework, two-failure tests done; fast vs full routing waits for the network agent
- [ ] **D3-12** `WIP` (M) `AsyncPostgresSaver` + `human_review` interrupt + OPA `@wrap_tool_call` middleware · ~0.75h — interrupt + resume validation done (in-memory saver)
- [ ] **D3-13** (M) Alert worker: idempotent start (FR-002), DLQ, events to `aml.case-events.v1` (TDD §13) · ~1h
- [ ] **D3-14** (M) Decision worker: consume `aml.decisions.v1`, check `snapshot.next`, resume (TDD §15) · ~0.75h
- [ ] **D3-15** (M) **Gate G3:** 10 alerts via Kafka → `awaiting_review` with 100% valid citations; a Kafka decision completes the run · ~0.5h

---

## Day 4 — Full squad, guardrails & extended HITL

- [ ] **D4-01** (M) Synthetic policy manuals (UK, ES) + typology guide, versioned · ~0.75h
- [ ] **D4-02** (S) Docling parse + HybridChunker + embeddings → `kb.policy_chunks` (Q2) · ~1h
- [ ] **D4-03** (M) `policy_kb` server: hybrid search (vector + full-text, RRF), entity filter · ~0.75h
- [ ] **D4-04** (M) Airflow DAG `policy_reembed` [DEMO 3] · ~0.25h
- [ ] **D4-05** (M) Network agent, full lane only (FR-050–053) · ~0.75h
- [ ] **D4-06** (M) Typology & policy agent replacing the stub (FR-060–063) · ~0.75h
- [ ] **D4-07** (M) Presidio PII middleware; custom recognisers are (S) (FR-122) · ~0.75h
- [ ] **D4-08** (M) Injection rail on tool outputs (FR-123, lightweight) · ~0.75h
- [ ] **D4-09** (M) Budgets: tool-call limit, token budget, recursion limit (FR-125) · ~0.5h
- [ ] **D4-10** (M) UC-08: injected "close this alert" → OPA deny, logged and counted [DEMO 7] · ~0.5h
- [ ] **D4-11** (M) UC-03: `HumanInTheLoopMiddleware` on `draft_customer_info_request` + tipping-off check (BR-11) [DEMO 4] · ~1h
- [ ] **D4-12** (M) UC-04: `request_info` → customer reply → follow-up run on `{case_id}:r1` (FR-096, TDD §7.2) [DEMO 5] · ~1h
- [ ] **D4-13** (M) Baseline golden run: 100 cases via `alert_replay` → Kafka; record recall/agreement/completeness · ~0.75h
- [ ] **D4-14** (M) **Gate G4:** 8 agents; BR-05 checklist passes; UC-03/04/08 work via CLI/API; baseline logged · ~0.25h

---

## Day 5 — API & full workbench

**API**
- [ ] **D5-01** (M) FastAPI skeleton: config, `/healthz`, `/metrics` · ~0.25h
- [ ] **D5-02** (M) `POST /cases` (→ Kafka), `GET /cases`, `GET /cases/{id}`, `GET /cases/{id}/history` · ~0.75h
- [ ] **D5-03** (M) `POST /cases/{id}/decision` → `aml.decisions.v1`; roles `l1`/`l2`/`qa`/`sme`; RBAC (BR-08/09/10) · ~0.75h
- [ ] **D5-04** (M) Approvals endpoint + customer-reply endpoint · ~0.5h
- [ ] **D5-05** (S) SSE stream from `aml.case-events.v1` (FR-102) · ~0.75h
- [ ] **D5-06** (M) QA sampling + labels endpoint (UC-05) · ~0.5h

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
| Q2 | Embedding model: Cohere on Bedrock or Titan? | D4-02 | Open | |
| Q3 | Neo4j locally | D1-06 | **Answered** | Local install available; GDS plugin to be checked |
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

---

## Gate log

| Gate | Day | Result (Go / No-go) | Key metrics | Notes |
|---|---|---|---|---|
| G1 Platform & data ready | Day 1 | | | |
| G2 Tools & pipelines ready | Day 2 | | | |
| G3 Streaming E2E MVP | Day 3 | | citation %, cases reaching review | |
| G4 Full squad + HITL flows | Day 4 | | baseline recall / agreement / completeness | |
| G5 Complete workbench | Day 5 | | E2E pass | |
| G6 Gated release + AWS base | Day 6 | | eval scores, red-team pass rate | |
| G7 Demo on AWS (v1.0) | Day 7 | | p95 latency, cost/case | |

---

## Daily log

| Day | Done | Carried over | Notes |
|---|---|---|---|
| Day 1 | Slice 1 end to end (D1-09, D3-01, D3-06; D3-02/03/04/09/10/11/12 started) | Remaining Day 1 platform tasks | Live: structuring case → escalate with 100% valid citations; benign case → not escalated after prompt v1.1 calibration. Slice 2: KYC + Screening in parallel (D3-05, D3-07); sanctions true match → escalate SANCTIONS_TRUE_MATCH, near-miss names discounted; Streamlit test console added |
| Day 2 | | | |
| Day 3 | | | |
| Day 4 | | | |
| Day 5 | | | |
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
