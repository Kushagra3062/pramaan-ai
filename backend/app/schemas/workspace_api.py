"""Safe authenticated-user, workspace-discovery, and workspace-overview responses."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CurrentUserResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    email: str
    display_name: str


class WorkspaceMembershipResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: uuid.UUID
    name: str
    slug: str
    role: str


class WorkspaceOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: uuid.UUID
    total_documents: int
    processing_documents: int
    ready_documents: int
    failed_documents: int
    extracted_facts: int
    requirements: int
    pending_reviews: int


class ActivityEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    event_type: str
    entity_type: str
    entity_id: uuid.UUID | None = None
    actor_user_id: uuid.UUID | None = None
    request_id: str | None = None
    created_at: datetime


class ModelUsageItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    provider: str
    model_id: str
    request_id: str | None = None
    input_tokens: int
    output_tokens: int
    latency_ms: float
    estimated_cost: float | None = None
    status: str
    analysis_id: uuid.UUID | None = None
    created_at: datetime


class UsageOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_input_tokens: int
    total_output_tokens: int
    total_requests: int
    items: list[ModelUsageItemResponse]
