"""Action Center workflow: idempotent drafts plus human approve/reject.

The database CHECK constraints require an approver/rejecter identity on every
APPROVED/REJECTED transition; these helpers enforce the same rule in Python so
API callers get a 4xx instead of an integrity error. No transition happens on
page render, model output, or failed request — only through these functions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.repositories.audit_eval_repo import AuditRepository
from app.db.repositories.governance_repo import ActionRepository
from app.models.enums import ActionPriority, ActionStatus
from app.models.governance import ActionItem


class ActionTransitionError(ValueError):
    """A safe, user-facing description of a rejected action transition."""


@dataclass(frozen=True)
class ActionOutcome:
    action: ActionItem
    created: bool


_TERMINAL = {ActionStatus.APPROVED.value, ActionStatus.REJECTED.value, ActionStatus.COMPLETED.value, ActionStatus.CANCELLED.value}


def create_action(
    session: Session,
    workspace_id: uuid.UUID,
    description: str,
    *,
    priority: str = ActionPriority.MEDIUM.value,
    proposed_owner: str | None = None,
    due_date: datetime | None = None,
    related_analysis_id: uuid.UUID | None = None,
    related_conflict_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    requested_by: uuid.UUID | None = None,
    request_id: str | None = None,
) -> ActionOutcome:
    """Create a PROPOSED draft; a repeated idempotency key returns the original."""
    repo = ActionRepository(session)
    if idempotency_key:
        existing = repo.get_by_idempotency_key(workspace_id, idempotency_key)
        if existing is not None:
            return ActionOutcome(action=existing, created=False)
    action = repo.create(
        workspace_id=workspace_id,
        description=description,
        priority=priority,
        proposed_owner=proposed_owner,
        due_date=due_date,
        related_analysis_id=related_analysis_id,
        related_conflict_id=related_conflict_id,
        idempotency_key=idempotency_key,
        status=ActionStatus.PROPOSED,
    )
    AuditRepository(session).log(
        workspace_id=workspace_id,
        actor_user_id=requested_by,
        event_type="action.proposed",
        entity_type="action_item",
        entity_id=action.id,
        request_id=request_id,
        metadata={"priority": action.priority, "has_conflict": related_conflict_id is not None},
    )
    return ActionOutcome(action=action, created=True)


def _ensure_mutable(action: ActionItem) -> None:
    if action.status in _TERMINAL:
        raise ActionTransitionError(f"action is already {action.status.lower()}")


def approve_action(
    session: Session,
    action: ActionItem,
    *,
    approved_by: uuid.UUID,
    rationale: str | None = None,
    request_id: str | None = None,
) -> ActionItem:
    _ensure_mutable(action)
    updated = ActionRepository(session).approve(action.id, approved_by, rationale)
    assert updated is not None
    AuditRepository(session).log(
        workspace_id=updated.workspace_id,
        actor_user_id=approved_by,
        event_type="action.approved",
        entity_type="action_item",
        entity_id=updated.id,
        request_id=request_id,
        metadata={"rationale_chars": len(rationale or "")},
    )
    return updated


def reject_action(
    session: Session,
    action: ActionItem,
    *,
    rejected_by: uuid.UUID,
    rationale: str | None = None,
    request_id: str | None = None,
) -> ActionItem:
    _ensure_mutable(action)
    updated = ActionRepository(session).reject(action.id, rejected_by, rationale)
    assert updated is not None
    AuditRepository(session).log(
        workspace_id=updated.workspace_id,
        actor_user_id=rejected_by,
        event_type="action.rejected",
        entity_type="action_item",
        entity_id=updated.id,
        request_id=request_id,
        metadata={"rationale_chars": len(rationale or "")},
    )
    return updated
