import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, FullTimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    ActionPriority,
    ActionStatus,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
)

if TYPE_CHECKING:
    from app.models.evidence_analysis import Analysis
    from app.models.fact_requirement import ExtractedFact
    from app.models.graph import GraphNode
    from app.models.workspace import User, Workspace


class Conflict(Base, UUIDPrimaryKeyMixin, FullTimestampMixin):
    __tablename__ = "conflicts"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    fact_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extracted_facts.id", ondelete="CASCADE"),
        nullable=False,
    )
    fact_b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extracted_facts.id", ondelete="CASCADE"),
        nullable=False,
    )
    conflict_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=ConflictSeverity.MEDIUM.value,
    )
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=ConflictStatus.OPEN.value,
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    graph_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("graph_nodes.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )

    __table_args__ = (
        CheckConstraint("fact_a_id != fact_b_id", name="ck_conflicts_no_self_pair"),
        CheckConstraint("fact_a_id < fact_b_id", name="ck_conflicts_ordered_pair"),
        CheckConstraint(
            f"conflict_type IN ('{ConflictType.DIRECT_CONTRADICTION.value}', '{ConflictType.NUMERIC_MISMATCH.value}', '{ConflictType.DEADLINE_MISMATCH.value}', '{ConflictType.RESPONSIBILITY_OVERLAP.value}', '{ConflictType.SCOPE_DISCREPANCY.value}')",
            name="ck_conflicts_type_valid",
        ),
        CheckConstraint(
            f"severity IN ('{ConflictSeverity.LOW.value}', '{ConflictSeverity.MEDIUM.value}', '{ConflictSeverity.HIGH.value}', '{ConflictSeverity.CRITICAL.value}')",
            name="ck_conflicts_severity_valid",
        ),
        CheckConstraint(
            f"status IN ('{ConflictStatus.OPEN.value}', '{ConflictStatus.IN_REVIEW.value}', '{ConflictStatus.RESOLVED.value}', '{ConflictStatus.DISMISSED.value}')",
            name="ck_conflicts_status_valid",
        ),
        UniqueConstraint("workspace_id", "fact_a_id", "fact_b_id", name="uq_conflicts_workspace_fact_pair"),
        Index("ix_conflicts_workspace_status", "workspace_id", "status"),
        Index("ix_conflicts_fact_a", "fact_a_id"),
        Index("ix_conflicts_fact_b", "fact_b_id"),
    )

    # Relationships
    workspace: Mapped["Workspace"] = relationship("Workspace")
    fact_a: Mapped["ExtractedFact"] = relationship("ExtractedFact", foreign_keys=[fact_a_id])
    fact_b: Mapped["ExtractedFact"] = relationship("ExtractedFact", foreign_keys=[fact_b_id])
    resolver: Mapped["User | None"] = relationship("User")
    graph_node: Mapped["GraphNode | None"] = relationship("GraphNode")


class ActionItem(Base, UUIDPrimaryKeyMixin, FullTimestampMixin):
    __tablename__ = "action_items"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    related_analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analyses.id", ondelete="SET NULL"),
        nullable=True,
    )
    related_conflict_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conflicts.id", ondelete="SET NULL"),
        nullable=True,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=ActionPriority.MEDIUM.value,
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=ActionStatus.PROPOSED.value,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    graph_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("graph_nodes.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )

    __table_args__ = (
        CheckConstraint(
            f"priority IN ('{ActionPriority.LOW.value}', '{ActionPriority.MEDIUM.value}', '{ActionPriority.HIGH.value}', '{ActionPriority.URGENT.value}')",
            name="ck_action_items_priority_valid",
        ),
        CheckConstraint(
            f"status IN ('{ActionStatus.PROPOSED.value}', '{ActionStatus.APPROVED.value}', '{ActionStatus.REJECTED.value}', '{ActionStatus.COMPLETED.value}', '{ActionStatus.CANCELLED.value}')",
            name="ck_action_items_status_valid",
        ),
        CheckConstraint(
            "status != 'APPROVED' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_actions_approved_requires_human",
        ),
        CheckConstraint(
            "status != 'REJECTED' OR (rejected_by IS NOT NULL AND rejected_at IS NOT NULL)",
            name="ck_actions_rejected_requires_human",
        ),
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_action_items_workspace_idempotency"),
        Index("ix_action_items_workspace_status", "workspace_id", "status"),
        Index("ix_action_items_analysis", "related_analysis_id"),
        Index("ix_action_items_conflict", "related_conflict_id"),
    )

    # Relationships
    workspace: Mapped["Workspace"] = relationship("Workspace")
    related_analysis: Mapped["Analysis | None"] = relationship("Analysis")
    related_conflict: Mapped["Conflict | None"] = relationship("Conflict")
    approver: Mapped["User | None"] = relationship("User", foreign_keys=[approved_by])
    rejecter: Mapped["User | None"] = relationship("User", foreign_keys=[rejected_by])
    graph_node: Mapped["GraphNode | None"] = relationship("GraphNode")
