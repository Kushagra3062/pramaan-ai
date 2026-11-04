"""Action Center API: idempotent drafts plus human approve/reject (FR-018/FR-019).

No action becomes approved on page render, model output, or failed request:
approval and rejection are explicit, role-checked, audit-logged transitions.
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
from app.db.engine import get_db
from app.db.repositories.governance_repo import ActionRepository
from app.models.enums import ActionPriority, ActionStatus
from app.models.governance import ActionItem
from app.schemas.governance_api import (
    ActionCreateRequest,
    ActionDecisionRequest,
    ActionResponse,
    PaginatedActionsResponse,
)
from app.services.action_service import (
    ActionTransitionError,
    approve_action,
    create_action,
    reject_action,
)

router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/actions",
    tags=["actions"],
    dependencies=[Depends(require_workspace_read)],
)

_VALID_STATUSES = {item.value for item in ActionStatus}
_VALID_PRIORITIES = {item.value for item in ActionPriority}


def _to_response(action: ActionItem) -> ActionResponse:
    return ActionResponse(
        id=action.id,
        workspace_id=action.workspace_id,
        description=action.description,
        priority=action.priority,
        status=action.status,
        proposed_owner=action.proposed_owner,
        due_date=action.due_date,
        related_analysis_id=action.related_analysis_id,
        related_conflict_id=action.related_conflict_id,
        idempotency_key=action.idempotency_key,
        decision_rationale=action.decision_rationale,
        approved_at=action.approved_at,
        rejected_at=action.rejected_at,
        created_at=action.created_at,
    )


def _get_scoped_action(session: Session, workspace_id: uuid.UUID, action_id: uuid.UUID) -> ActionItem:
    action = ActionRepository(session).get_by_id(action_id)
    if action is None or action.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Action not found.")
    return action


@router.get("", response_model=PaginatedActionsResponse)
def list_actions(
    workspace_id: uuid.UUID,
    session: Session = Depends(get_db),
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PaginatedActionsResponse:
    if status_filter and status_filter not in _VALID_STATUSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown action status.")
    items = ActionRepository(session).list_by_workspace(workspace_id, status=status_filter, limit=limit, offset=offset)
    count_stmt = select(func.count(ActionItem.id)).where(ActionItem.workspace_id == workspace_id)
    if status_filter:
        count_stmt = count_stmt.where(ActionItem.status == status_filter)
    total = session.scalar(count_stmt) or 0
    return PaginatedActionsResponse(items=[_to_response(item) for item in items], total=total, limit=limit, offset=offset)


@router.get("/{action_id}", response_model=ActionResponse)
def get_action(
    workspace_id: uuid.UUID,
    action_id: uuid.UUID,
    session: Session = Depends(get_db),
) -> ActionResponse:
    return _to_response(_get_scoped_action(session, workspace_id, action_id))


@router.post("", response_model=ActionResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_workspace_write)])
def propose_action(
    workspace_id: uuid.UUID,
    body: ActionCreateRequest,
    request: Request,
    session: Session = Depends(get_db),
    principal: AuthenticatedUser = Depends(get_authenticated_user),
) -> ActionResponse:
    if body.priority not in _VALID_PRIORITIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown action priority.")
    outcome = create_action(
        session, workspace_id, body.description.strip(),
        priority=body.priority, proposed_owner=(body.proposed_owner or "").strip() or None,
        due_date=body.due_date, related_analysis_id=body.related_analysis_id,
        related_conflict_id=body.related_conflict_id, idempotency_key=(body.idempotency_key or "").strip() or None,
        requested_by=principal.user.id, request_id=request.headers.get("X-Request-ID"),
    )
    session.commit()
    return _to_response(outcome.action)


@router.post("/{action_id}/approve", response_model=ActionResponse, dependencies=[Depends(require_workspace_write)])
def approve(
    workspace_id: uuid.UUID,
    action_id: uuid.UUID,
    body: ActionDecisionRequest,
    request: Request,
    session: Session = Depends(get_db),
    principal: AuthenticatedUser = Depends(get_authenticated_user),
) -> ActionResponse:
    action = _get_scoped_action(session, workspace_id, action_id)
    try:
        updated = approve_action(
            session, action, approved_by=principal.user.id,
            rationale=(body.rationale or "").strip() or None, request_id=request.headers.get("X-Request-ID"),
        )
    except ActionTransitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    session.commit()
    return _to_response(updated)


@router.post("/{action_id}/reject", response_model=ActionResponse, dependencies=[Depends(require_workspace_write)])
def reject(
    workspace_id: uuid.UUID,
    action_id: uuid.UUID,
    body: ActionDecisionRequest,
    request: Request,
    session: Session = Depends(get_db),
    principal: AuthenticatedUser = Depends(get_authenticated_user),
) -> ActionResponse:
    action = _get_scoped_action(session, workspace_id, action_id)
    try:
        updated = reject_action(
            session, action, rejected_by=principal.user.id,
            rationale=(body.rationale or "").strip() or None, request_id=request.headers.get("X-Request-ID"),
        )
    except ActionTransitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    session.commit()
    return _to_response(updated)
