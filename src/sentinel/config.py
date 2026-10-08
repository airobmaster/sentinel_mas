"""Runtime settings. Override any field with an env var (prefix SENTINEL_) or a .env file,
e.g. SENTINEL_MODEL_TXN=deepseek.v3-v1:0. See .env.example for the Docker-backed setup."""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
MCP_TOKEN_AUDIENCE = "sentinel-mcp"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SENTINEL_", env_file=".env", extra="ignore", protected_namespaces=()
    )

    # Models (Bedrock, eu-west-2). Claude Sonnet/Opus 5.5 are not enabled on this account.
    aws_region: str = "eu-west-2"
    model_kyc: str = "deepseek.v3.2"
    model_txn: str = "deepseek.v3.2"
    model_screening: str = "deepseek.v3.2"
    model_network: str = "deepseek.v3.2"
    model_typology: str = "deepseek.v3.2"
    model_narrative: str = "deepseek.v3.2"
    model_qa: str = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"  # must differ from the narrative model
    qa_llm_critic: bool = True  # code checks always run; the LLM critic can be switched off (offline tests)
    model_embedding: str = "cohere.embed-multilingual-v3"  # 1024 dims, English + Spanish policies

    # Policy documents (chunked into the knowledge base)
    policy_dir: Path = REPO_ROOT / "data" / "policies"

    # Graph (Neo4j). Unset URI = in-memory graph built from the dataset (offline tests, in-process mode).
    neo4j_uri: str | None = None
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"
    graph_as_of: str = "2026-03-31"  # reference date of the synthetic dataset for network scores
    graph_lookback_days: int = 90

    # Data: "json" reads dataset files (no services needed); "postgres" reads the loaded database.
    data_backend: Literal["json", "postgres"] = "json"
    dataset_paths: list[Path] = [
        REPO_ROOT / "data" / "fixtures" / "cases.json",
        REPO_ROOT / "data" / "generated" / "dataset.json",  # from `sentinel data generate`; optional
    ]
    pg_dsn: str = "postgresql://sentinel:sentinel@localhost:5432/sentinel"

    # Tools: "local" runs them in-process; "mcp" calls the MCP servers over HTTP.
    tool_mode: Literal["local", "mcp"] = "local"
    mcp_urls: dict[str, str] = {
        "case_mgmt": "http://localhost:8101/mcp/",
        "kyc_profile": "http://localhost:8102/mcp/",
        "txn_history": "http://localhost:8103/mcp/",
        "screening": "http://localhost:8104/mcp/",
        "graph_query": "http://localhost:8105/mcp/",
        "policy_kb": "http://localhost:8106/mcp/",
    }
    mcp_require_auth: bool = True  # servers reject calls without a valid service token
    mcp_dev_secret: str = "dev-only-signing-key-not-for-production"  # HS256 dev tokens (TDD §8.1 [LOCAL])

    # Policy: OPA base URL, e.g. http://localhost:8181. Unset = no policy check (offline dev only).
    opa_url: str | None = None

    # Streaming (Kafka). "plaintext" for the local broker; "msk_iam" for Amazon MSK Serverless (FR-001).
    kafka_bootstrap: str = "localhost:9092"
    kafka_security: Literal["plaintext", "msk_iam"] = "plaintext"
    kafka_partitions: int = 6
    worker_max_attempts: int = 2  # per message, on top of node-level retries; then the DLQ

    # Business rules
    full_lane_amount: float = 50_000  # BR-02 full-lane threshold, entity currency
    cash_reporting_threshold: float = 10_000  # structuring detector threshold
    name_match_threshold: float = 85  # sanctions/PEP fuzzy match score (0-100)

    # Budgets (FR-125)
    graph_recursion_limit: int = 40  # outer graph super-steps (TDD §5.4)
    # Backstop only: the call limits below end a run gracefully; each model call costs several graph steps
    agent_recursion_limit: int = 60
    max_tool_calls_per_agent: int = 12  # per specialist run
    max_model_calls_per_agent: int = 14  # per specialist run
    case_token_budget: int = 400_000  # input + output tokens across all agents; over budget -> human

    # Guardrails (FR-122/123)
    pii_redaction: bool = True  # models see reversible tokens instead of PII
    presidio_url: str | None = "http://localhost:5002"  # Presidio analyzer service (en_core_web_lg)
    pii_score_threshold: float = 0.7
    pii_token_key: str = "dev-only-pii-token-key"  # HMAC key so tokens cannot be reversed by guessing


settings = Settings()
