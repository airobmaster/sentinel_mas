"""Runtime settings. Override any field with an env var, e.g. SENTINEL_MODEL_TXN=deepseek.v3-v1:0."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


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

    # Data (JSON fixtures; replaced by Postgres + MCP tools in a later slice)
    fixtures_path: Path = REPO_ROOT / "data" / "fixtures" / "cases.json"

    # Business rules
    full_lane_amount: float = 50_000  # BR-02 full-lane threshold, entity currency
    cash_reporting_threshold: float = 10_000  # structuring detector threshold
    name_match_threshold: float = 85  # sanctions/PEP fuzzy match score (0-100)

    # Budgets
    graph_recursion_limit: int = 40  # outer graph super-steps (TDD §5.4)
    agent_recursion_limit: int = 25  # bounds each specialist's inner tool loop


settings = Settings()
