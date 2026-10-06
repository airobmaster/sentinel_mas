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
    model_narrative: str = "deepseek.v3.2"

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

    # Budgets
    graph_recursion_limit: int = 40  # outer graph super-steps (TDD §5.4)
    agent_recursion_limit: int = 25  # bounds each specialist's inner tool loop


settings = Settings()
