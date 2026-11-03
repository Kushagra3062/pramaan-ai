"""Safe authenticated-user, workspace-discovery, and workspace-overview responses."""

from __future__ import annotations

import uuid

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
