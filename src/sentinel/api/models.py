"""Request bodies. Who acted (investigator / approver / reviewer) always comes from the token, never
from the body, so it cannot be spoofed."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["close", "escalate", "request_info"]
    reason_code: str
    narrative_edits: str | None = Field(None, max_length=10_000)


class ApprovalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "edit", "reject"]
    message: str | None = Field(None, max_length=5_000)
    questions: list[str] | None = Field(None, max_length=5)


class ReplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply_text: str = Field(min_length=1, max_length=5_000)


class QALabel(BaseModel):
    """QA rubric (FR-106): 1 = poor, 5 = excellent."""

    model_config = ConfigDict(extra="forbid")

    evidence_complete: int = Field(ge=1, le=5, description="All relevant evidence gathered")
    citations_accurate: int = Field(ge=1, le=5, description="Claims match the cited evidence")
    recommendation_sound: int = Field(ge=1, le=5, description="Recommendation follows from evidence and policy")
    narrative_clear: int = Field(ge=1, le=5, description="Narrative is clear, neutral and complete")
    decision_correct: bool = Field(description="The investigator's final decision was right")
    comment: str | None = Field(None, max_length=2_000)
