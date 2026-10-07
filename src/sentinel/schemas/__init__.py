"""Pydantic outputs for the agents (TDD §6.2) and the decision reason codes (Functional Spec §9)."""

from typing import Literal

from pydantic import BaseModel, Field

REASON_CODES: dict[str, list[str]] = {
    "close": ["FP_EXPLAINED_ACTIVITY", "FP_SCREENING_FALSE_MATCH", "FP_KNOWN_BUSINESS_PATTERN", "FP_DATA_ERROR"],
    "escalate": [
        "STRUCTURING_CONFIRMED",
        "PASS_THROUGH",
        "MULE_NETWORK",
        "SANCTIONS_TRUE_MATCH",
        "PEP_UNEXPLAINED",
        "PROFILE_MISMATCH",
        "OTHER_SUSPICION",
    ],
    "request_info": ["SOURCE_OF_FUNDS", "BUSINESS_PURPOSE", "COUNTERPARTY_RELATIONSHIP"],
}


class Observation(BaseModel):
    description: str
    evidence_ids: list[str] = Field(description="Evidence IDs returned by the tools, e.g. crm:CRM-0042-01")


class KycFindings(BaseModel):
    risk_rating: str
    business_purpose: str
    expected_vs_actual: list[Observation] = Field(
        description="How the alerted activity compares with the customer's expected activity"
    )
    discrepancies: list[Observation] = Field(
        description="Conflicts between the profile, CRM notes and the alerted activity; empty if none"
    )
    summary: str = Field(description="2-3 sentences on what the customer profile shows")


class ScreeningHit(BaseModel):
    list_name: str = Field(description="OFSI, PEP, ...")
    entry_id: str
    match_score: float
    is_true_match: bool = Field(description="True only if the identifiers (DOB, nationality) also agree")
    reason: str
    evidence_ids: list[str]


class MediaFinding(BaseModel):
    article_id: str
    is_about_customer: bool
    reason: str
    evidence_ids: list[str]


class ScreeningFindings(BaseModel):
    hits: list[ScreeningHit] = Field(description="Every sanctions/PEP candidate returned, true or false match")
    adverse_media: list[MediaFinding] = Field(description="Every article returned, relevant or not")
    summary: str


class RedFlag(BaseModel):
    code: str = Field(description="Short code, e.g. STRUCTURING, HIGH_CASH_RATIO, RAPID_OUTFLOW, PROFILE_MISMATCH")
    description: str
    evidence_ids: list[str] = Field(description="Evidence IDs returned by the tools, e.g. txn:TXN-1006")


class TxnMetrics(BaseModel):
    velocity: float | None = Field(None, description="Transactions per 30 days")
    cash_ratio: float | None = Field(None, description="Cash credits / all credits")
    passthrough_ratio: float | None = Field(None, description="Debits / credits")
    peer_percentile: float | None = Field(None, description="Leave empty: no peer data yet")


class TxnFindings(BaseModel):
    metrics: TxnMetrics
    red_flags: list[RedFlag]
    summary: str = Field(description="2-3 sentences on what the transactions show")


class RelatedParty(BaseModel):
    customer_id: str
    relationship: str = Field(description="How they are linked, e.g. 'shares device DEV-R001', 'received transfer'")
    mule_score: float | None = None
    evidence_ids: list[str]


class NetworkFindings(BaseModel):
    assessment: Literal["isolated", "benign_links", "mule_network_suspected"] = Field(
        description="isolated: no links; benign_links: links consistent with family/household or ordinary use; "
                    "mule_network_suspected: shared devices or forwarding consistent with a mule ring")
    mule_score: float | None = Field(None, description="The customer's own mule score from the graph tools")
    community_id: str | None = None
    related_parties: list[RelatedParty]
    shared_devices: list[Observation]
    summary: str


class TypologyMatch(BaseModel):
    code: Literal["STRUCT", "PASSTHRU", "MULE_NETWORK", "HRJ", "SANCTIONS", "PEP", "BENIGN"]
    confidence: float = Field(ge=0, le=1)
    rationale: str
    evidence_ids: list[str] = Field(description="Case evidence supporting the match (txn:, cust:, list:, graph: ...)")
    policy_refs: list[str] = Field(description="Policy evidence IDs, e.g. policy:AML-UK@3.2#4.3")


class TypologyAssessment(BaseModel):
    typologies: list[TypologyMatch] = Field(description="Typologies the findings match, strongest first")
    risk_score: int = Field(ge=0, le=100)
    recommendation: Literal["close", "escalate", "request_info"]
    reason_code: str = Field(description="A reason code allowed for the recommendation")
    rationale: str = Field(description="Why this recommendation follows from the findings and the cited policy")
    policy_refs: list[str] = Field(description="Every policy evidence ID the recommendation relies on")


class QAFinding(BaseModel):
    target_agent: Literal["kyc", "txn", "screening", "network", "typology", "narrative"]
    severity: Literal["blocker", "major", "minor"]
    description: str


class QAReport(BaseModel):
    passed: bool
    issues: list[QAFinding] = Field(description="Only problems that matter for the investigator's decision")
    checks: dict[str, bool] = Field(description="e.g. claims_supported, recommendation_consistent, "
                                                "red_flags_covered, no_tipping_off, neutral_language")


class CustomerInfoRequest(BaseModel):
    """UC-03: customer-facing request for information. Must never reveal suspicion (BR-11)."""

    questions: list[str] = Field(min_length=1, max_length=5, description="Plain-language questions")
    message: str = Field(description="A short, polite message to the customer that introduces the questions")


class Claim(BaseModel):
    text: str = Field(description="One factual statement")
    evidence_ids: list[str] = Field(description="At least one ID from the evidence catalogue")


class NarrativeDraft(BaseModel):
    summary: str
    claims: list[Claim]
    recommendation: Literal["close", "escalate", "request_info"]
    reason_code: str = Field(description="A reason code allowed for the recommendation")
    open_questions: list[str] = Field(default_factory=list)
