"""Request/response schemas for Investigate analyses and Decision Packets."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class InvestigateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=3, max_length=2000)
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)


class AnalysisSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    workspace_id: uuid.UUID
    question: str
    status: str
    model_id: str | None = None
    prompt_version: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class PaginatedAnalysesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AnalysisSummaryResponse]
    total: int
    limit: int
    offset: int


class ClaimEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_filename: str
    page_start: int
    page_end: int
    excerpt: str
    evidence_role: str


class AnalysisClaimResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    claim_text: str
    support_status: str
    evidence: list[ClaimEvidenceResponse]


class AnalysisDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    workspace_id: uuid.UUID
    question: str
    status: str
    model_id: str | None = None
    prompt_version: str | None = None
    result: dict[str, Any] | None = None
    claims: list[AnalysisClaimResponse]
    created_at: datetime
    completed_at: datetime | None = None
