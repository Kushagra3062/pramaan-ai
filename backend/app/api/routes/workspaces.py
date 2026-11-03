"""Authenticated identity and workspace discovery; never trusts browser-supplied roles."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth import AuthenticatedUser, get_authenticated_user, require_workspace_read
from app.db.engine import get_db
from app.models.document import Document
from app.models.enums import DocumentProcessingStatus, RequirementStatus
from app.models.fact_requirement import ExtractedFact, Requirement
from app.models.workspace import Workspace, WorkspaceMember
from app.schemas.workspace_api import (
    CurrentUserResponse,
    WorkspaceMembershipResponse,
    WorkspaceOverviewResponse,
)

router = APIRouter(tags=["workspaces"])


@router.get("/api/v1/me", response_model=CurrentUserResponse)
def get_current_user(principal: AuthenticatedUser = Depends(get_authenticated_user)) -> CurrentUserResponse:
    return CurrentUserResponse(
        user_id=principal.user.id,
        email=principal.user.email,
        display_name=principal.user.display_name,
    )


@router.get("/api/v1/workspaces", response_model=list[WorkspaceMembershipResponse])
def list_workspaces(
    principal: AuthenticatedUser = Depends(get_authenticated_user),
    session: Session = Depends(get_db),
) -> list[WorkspaceMembershipResponse]:
    rows = session.execute(
        select(WorkspaceMember, Workspace)
        .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
        .where(WorkspaceMember.user_id == principal.user.id)
        .order_by(Workspace.name.asc(), Workspace.id.asc())
    ).all()
    return [
        WorkspaceMembershipResponse(workspace_id=workspace.id, name=workspace.name, slug=workspace.slug, role=member.role)
        for member, workspace in rows
    ]


@router.get(
    "/api/v1/workspaces/{workspace_id}/overview",
    response_model=WorkspaceOverviewResponse,
    dependencies=[Depends(require_workspace_read)],
)
def get_workspace_overview(workspace_id: uuid.UUID, session: Session = Depends(get_db)) -> WorkspaceOverviewResponse:
    """Aggregate only safe workspace counts with SQL aggregates, never document rows."""
    document_counts = session.execute(
        select(
            func.count(Document.id),
            func.count(Document.id).filter(
                Document.processing_status.in_(
                    [
                        DocumentProcessingStatus.UPLOADED.value,
                        DocumentProcessingStatus.VALIDATING.value,
                        DocumentProcessingStatus.PROCESSING.value,
                        DocumentProcessingStatus.NEEDS_OCR.value,
                    ]
                )
            ),
            func.count(Document.id).filter(Document.processing_status == DocumentProcessingStatus.READY.value),
            func.count(Document.id).filter(Document.processing_status == DocumentProcessingStatus.FAILED.value),
        ).where(Document.workspace_id == workspace_id, Document.deleted_at.is_(None))
    ).one()
    fact_count = session.scalar(select(func.count(ExtractedFact.id)).where(ExtractedFact.workspace_id == workspace_id)) or 0
    requirement_count = session.scalar(select(func.count(Requirement.id)).where(Requirement.workspace_id == workspace_id)) or 0
    pending_review_count = session.scalar(
        select(func.count(Requirement.id)).where(
            Requirement.workspace_id == workspace_id,
            Requirement.status == RequirementStatus.DRAFT.value,
        )
    ) or 0
    return WorkspaceOverviewResponse(
        workspace_id=workspace_id,
        total_documents=int(document_counts[0] or 0),
        processing_documents=int(document_counts[1] or 0),
        ready_documents=int(document_counts[2] or 0),
        failed_documents=int(document_counts[3] or 0),
        extracted_facts=int(fact_count),
        requirements=int(requirement_count),
        pending_reviews=int(pending_review_count),
    )
