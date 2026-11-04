"""Changes & Conflicts API: candidate detection plus human resolution (FR-016).

Detection is deterministic and creates OPEN candidates only; resolution and
dismissal require an authenticated workspace member with write access and are
audit-logged with actor, timestamp and note.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth import (
    AuthenticatedUser,
    get_authenticated_user,
    require_workspace_read,
    require_workspace_write,
)
from app.core import metrics
from app.db.engine import get_db
from app.db.repositories.audit_eval_repo import AuditRepository
from app.db.repositories.governance_repo import ConflictRepository
from app.models.enums import ConflictStatus
from app.models.governance import Conflict
from app.schemas.governance_api import (
    ConflictDetectionResponse,
    ConflictFactSide,
    ConflictResolutionRequest,
    ConflictResponse,
    PaginatedConflictsResponse,
)
from app.services.conflict_service import detect_conflict_candidates

router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/conflicts",
    tags=["conflicts"],
    dependencies=[Depends(require_workspace_read)],
)

_VALID_STATUSES = {item.value for item in ConflictStatus}


def _side(fact) -> ConflictFactSide:
    return ConflictFactSide(
        fact_id=fact.id,
        document_id=fact.document_id,
        document_filename=fact.document.original_filename if fact.document else "Unknown document",
        fact_type=fact.fact_type,
        raw_value=fact.raw_value,
        source_quote=fact.source_quote,
        page_number=fact.page.page_number if fact.page else None,
    )


def _to_response(conflict: Conflict) -> ConflictResponse:
    return ConflictResponse(
        id=conflict.id,
        workspace_id=conflict.workspace_id,
        conflict_type=conflict.conflict_type,
        severity=conflict.severity,
        status=conflict.status,
        explanation=conflict.explanation,
        resolution_note=conflict.resolution_note,
        resolved_at=conflict.resolved_at,
        created_at=conflict.created_at,
        fact_a=_side(conflict.fact_a),
        fact_b=_side(conflict.fact_b),
    )


def _get_scoped_conflict(session: Session, workspace_id: uuid.UUID, conflict_id: uuid.UUID) -> Conflict:
    conflict = ConflictRepository(session).get_by_id(conflict_id)
    if conflict is None or conflict.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Conflict not found.")
    return conflict


@router.get("", response_model=PaginatedConflictsResponse)
def list_conflicts(
    workspace_id: uuid.UUID,
    session: Session = Depends(get_db),
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PaginatedConflictsResponse:
    if status_filter and status_filter not in _VALID_STATUSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown conflict status.")
    repo = ConflictRepository(session)
    items = repo.list_by_workspace(workspace_id, status=status_filter, limit=limit, offset=offset)
    count_stmt = select(func.count(Conflict.id)).where(Conflict.workspace_id == workspace_id)
    if status_filter:
        count_stmt = count_stmt.where(Conflict.status == status_filter)
    total = session.scalar(count_stmt) or 0
    return PaginatedConflictsResponse(items=[_to_response(item) for item in items], total=total, limit=limit, offset=offset)


@router.get("/{conflict_id}", response_model=ConflictResponse)
def get_conflict(
    workspace_id: uuid.UUID,
    conflict_id: uuid.UUID,
    session: Session = Depends(get_db),
) -> ConflictResponse:
    return _to_response(_get_scoped_conflict(session, workspace_id, conflict_id))


@router.post("/detect", response_model=ConflictDetectionResponse, dependencies=[Depends(require_workspace_write)])
def run_detection(
    workspace_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_db),
    principal: AuthenticatedUser = Depends(get_authenticated_user),
) -> ConflictDetectionResponse:
    request_id = request.headers.get("X-Request-ID")
    summary, created = detect_conflict_candidates(
        session, workspace_id, requested_by=principal.user.id, request_id=request_id
    )
    session.commit()
    metrics.record_metric(metrics.METRIC_HTTP_REQUESTS, labels={"method": "POST", "route": "conflicts.detect", "status_class": "2xx"})
    return ConflictDetectionResponse(
        facts_scanned=summary.facts_scanned,
        pairs_compared=summary.pairs_compared,
        candidates_created=summary.candidates_created,
        candidates_skipped_existing=summary.candidates_skipped_existing,
        conflicts=[_to_response(item) for item in created],
    )


def _transition(
    session: Session,
    conflict: Conflict,
    principal: AuthenticatedUser,
    request_id: str | None,
    action: str,
    note: str,
) -> Conflict:
    repo = ConflictRepository(session)
    if action == "resolve":
        updated = repo.resolve(conflict.id, principal.user.id, note)
    else:
        updated = repo.dismiss(conflict.id, principal.user.id, note)
    assert updated is not None
    AuditRepository(session).log(
        event_type=f"conflict.{action}d" if action == "resolve" else "conflict.dismissed",
        entity_type="conflict",
        workspace_id=conflict.workspace_id,
        actor_user_id=principal.user.id,
        entity_id=conflict.id,
        request_id=request_id,
        metadata={"note_chars": len(note)},
    )
    session.commit()
    return updated


@router.post("/{conflict_id}/resolve", response_model=ConflictResponse, dependencies=[Depends(require_workspace_write)])
def resolve_conflict(
    workspace_id: uuid.UUID,
    conflict_id: uuid.UUID,
    body: ConflictResolutionRequest,
    request: Request,
    session: Session = Depends(get_db),
    principal: AuthenticatedUser = Depends(get_authenticated_user),
) -> ConflictResponse:
    conflict = _get_scoped_conflict(session, workspace_id, conflict_id)
    if conflict.status == ConflictStatus.RESOLVED.value:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Conflict is already resolved.")
    return _to_response(_transition(session, conflict, principal, request.headers.get("X-Request-ID"), "resolve", body.note))


@router.post("/{conflict_id}/dismiss", response_model=ConflictResponse, dependencies=[Depends(require_workspace_write)])
def dismiss_conflict(
    workspace_id: uuid.UUID,
    conflict_id: uuid.UUID,
    body: ConflictResolutionRequest,
    request: Request,
    session: Session = Depends(get_db),
    principal: AuthenticatedUser = Depends(get_authenticated_user),
) -> ConflictResponse:
    conflict = _get_scoped_conflict(session, workspace_id, conflict_id)
    if conflict.status in {ConflictStatus.RESOLVED.value, ConflictStatus.DISMISSED.value}:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Conflict is already closed.")
    return _to_response(_transition(session, conflict, principal, request.headers.get("X-Request-ID"), "dismiss", body.note))
