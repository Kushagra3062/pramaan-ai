"""Strict schemas for the Investigate answer contract (FR-012/FR-013/FR-015)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AnswerStatus = Literal["supported", "mixed_evidence", "insufficient_evidence"]


class AnswerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)
    chunk_ids: list[str] = Field(default_factory=list, max_length=8)
    supported: bool = True


class ProposedActionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=1000)
    owner: str | None = Field(default=None, max_length=255)
    due_date: str | None = Field(default=None, max_length=64)
    chunk_ids: list[str] = Field(default_factory=list, max_length=8)


class AnalysisAnswer(BaseModel):
    """Exact JSON shape the model must return. No extra keys allowed."""

    model_config = ConfigDict(extra="forbid")

    status: AnswerStatus
    answer: str = Field(min_length=1, max_length=4000)
    claims: list[AnswerClaim] = Field(default_factory=list, max_length=20)
    unknowns: list[str] = Field(default_factory=list, max_length=10)
    proposed_actions: list[ProposedActionDraft] = Field(default_factory=list, max_length=10)
