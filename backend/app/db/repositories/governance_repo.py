import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.repositories.base import BaseRepository
from app.models.enums import (
    ActionPriority,
    ActionStatus,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
)
from app.models.governance import ActionItem, Conflict


class ConflictRepository(BaseRepository[Conflict]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Conflict)

    def create(
        self,
        workspace_id: uuid.UUID,
        fact_a_id: uuid.UUID,
        fact_b_id: uuid.UUID,
        conflict_type: str | ConflictType,
        explanation: str,
        severity: str | ConflictSeverity = ConflictSeverity.MEDIUM.value,
        status: str | ConflictStatus = ConflictStatus.OPEN.value,
        graph_node_id: uuid.UUID | None = None,
    ) -> Conflict:
        # Enforce canonical ordering: fact_a_id < fact_b_id
        first_id, second_id = (
            (fact_a_id, fact_b_id) if fact_a_id < fact_b_id else (fact_b_id, fact_a_id)
        )
        conflict = Conflict(
            workspace_id=workspace_id,
            fact_a_id=first_id,
            fact_b_id=second_id,
            conflict_type=conflict_type.value if isinstance(conflict_type, ConflictType) else conflict_type,
            severity=severity.value if isinstance(severity, ConflictSeverity) else severity,
            explanation=explanation,
            status=status.value if isinstance(status, ConflictStatus) else status,
            graph_node_id=graph_node_id,
        )
        return self.add(conflict)

    def get_by_fact_pair(
        self,
        workspace_id: uuid.UUID,
        fact_a_id: uuid.UUID,
        fact_b_id: uuid.UUID,
    ) -> Conflict | None:
        first_id, second_id = (
            (fact_a_id, fact_b_id) if fact_a_id < fact_b_id else (fact_b_id, fact_a_id)
        )
        stmt = select(Conflict).where(
            Conflict.workspace_id == workspace_id,
            Conflict.fact_a_id == first_id,
            Conflict.fact_b_id == second_id,
        )
        return self.session.scalars(stmt).first()

    def list_by_workspace(
        self,
        workspace_id: uuid.UUID,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Conflict]:
        stmt = select(Conflict).where(Conflict.workspace_id == workspace_id)
        if status:
            stmt = stmt.where(Conflict.status == status)
        stmt = stmt.order_by(Conflict.created_at.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(stmt).all())

    def resolve(
        self,
        conflict_id: uuid.UUID,
        resolved_by: uuid.UUID,
        resolution_note: str,
    ) -> Conflict | None:
        conflict = self.get_by_id(conflict_id)
        if conflict is not None:
            conflict.status = ConflictStatus.RESOLVED.value
            conflict.resolved_by = resolved_by
            conflict.resolved_at = datetime.now(UTC)
            conflict.resolution_note = resolution_note
            self.session.flush()
        return conflict

    def dismiss(
        self,
        conflict_id: uuid.UUID,
        dismissed_by: uuid.UUID,
        reason: str,
    ) -> Conflict | None:
        conflict = self.get_by_id(conflict_id)
        if conflict is not None:
            conflict.status = ConflictStatus.DISMISSED.value
            conflict.resolved_by = dismissed_by
            conflict.resolved_at = datetime.now(UTC)
            conflict.resolution_note = reason
            self.session.flush()
        return conflict


class ActionRepository(BaseRepository[ActionItem]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, ActionItem)

    def create(
        self,
        workspace_id: uuid.UUID,
        description: str,
        priority: str | ActionPriority = ActionPriority.MEDIUM.value,
        proposed_owner: str | None = None,
        due_date: datetime | None = None,
        related_analysis_id: uuid.UUID | None = None,
        related_conflict_id: uuid.UUID | None = None,
        idempotency_key: str | None = None,
        status: str | ActionStatus = ActionStatus.PROPOSED.value,
        graph_node_id: uuid.UUID | None = None,
    ) -> ActionItem:
        action = ActionItem(
            workspace_id=workspace_id,
            description=description,
            priority=priority.value if isinstance(priority, ActionPriority) else priority,
            proposed_owner=proposed_owner,
            due_date=due_date,
            related_analysis_id=related_analysis_id,
            related_conflict_id=related_conflict_id,
            idempotency_key=idempotency_key,
            status=status.value if isinstance(status, ActionStatus) else status,
            graph_node_id=graph_node_id,
        )
        return self.add(action)

    def get_by_idempotency_key(self, workspace_id: uuid.UUID, idempotency_key: str) -> ActionItem | None:
        stmt = select(ActionItem).where(
            ActionItem.workspace_id == workspace_id,
            ActionItem.idempotency_key == idempotency_key,
        )
        return self.session.scalars(stmt).first()

    def list_by_workspace(
        self,
        workspace_id: uuid.UUID,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ActionItem]:
        stmt = select(ActionItem).where(ActionItem.workspace_id == workspace_id)
        if status:
            stmt = stmt.where(ActionItem.status == status)
        stmt = stmt.order_by(ActionItem.created_at.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(stmt).all())

    def approve(
        self,
        action_id: uuid.UUID,
        approved_by: uuid.UUID,
        rationale: str | None = None,
    ) -> ActionItem | None:
        action = self.get_by_id(action_id)
        if action is not None:
            action.status = ActionStatus.APPROVED.value
            action.approved_by = approved_by
            action.approved_at = datetime.now(UTC)
            if rationale is not None:
                action.decision_rationale = rationale
            self.session.flush()
        return action

    def reject(
        self,
        action_id: uuid.UUID,
        rejected_by: uuid.UUID,
        rationale: str | None = None,
    ) -> ActionItem | None:
        action = self.get_by_id(action_id)
        if action is not None:
            action.status = ActionStatus.REJECTED.value
            action.rejected_by = rejected_by
            action.rejected_at = datetime.now(UTC)
            if rationale is not None:
                action.decision_rationale = rationale
            self.session.flush()
        return action
