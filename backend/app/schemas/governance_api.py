"""Response schemas for conflicts and actions (Changes & Action Center)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ConflictFactSide(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: uuid.UUID
    document_id: uuid.UUID
    document_filename: str
    fact_type: str
    raw_value: str
    source_quote: str
    page_number: int | None = None


class ConflictResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    workspace_id: uuid.UUID
    conflict_type: str
    severity: str
    status: str
    explanation: str
    resolution_note: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime
    fact_a: ConflictFactSide
    fact_b: ConflictFactSide


class PaginatedConflictsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ConflictResponse]
    total: int
    limit: int
    offset: int


class ConflictDetectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facts_scanned: int
    pairs_compared: int
    candidates_created: int
    candidates_skipped_existing: int
    conflicts: list[ConflictResponse]


class ConflictResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=1, max_length=2000)


class ActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    workspace_id: uuid.UUID
    description: str
    priority: str
    status: str
    proposed_owner: str | None = None
    due_date: datetime | None = None
    related_analysis_id: uuid.UUID | None = None
    related_conflict_id: uuid.UUID | None = None
    idempotency_key: str | None = None
    decision_rationale: str | None = None
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    created_at: datetime


class PaginatedActionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ActionResponse]
    total: int
    limit: int
    offset: int


class ActionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=4000)
    priority: str = Field(default="MEDIUM", max_length=20)
    proposed_owner: str | None = Field(default=None, max_length=255)
    due_date: datetime | None = None
    related_analysis_id: uuid.UUID | None = None
    related_conflict_id: uuid.UUID | None = None
    idempotency_key: str | None = Field(default=None, max_length=255)


class ActionDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rationale: str | None = Field(default=None, max_length=2000)
