import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import EvaluationCaseStatus, EvaluationRunStatus, ModelUsageStatus

if TYPE_CHECKING:
    from app.models.evidence_analysis import Analysis
    from app.models.workspace import User, Workspace


class AuditEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "audit_events"

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_audit_events_workspace_created", "workspace_id", text("created_at DESC")),
        Index("ix_audit_events_request_id", "request_id"),
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
    )

    # Relationships
    workspace: Mapped["Workspace | None"] = relationship("Workspace")
    actor: Mapped["User | None"] = relationship("User")


class ModelUsage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "model_usage"

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
    )
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analyses.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    estimated_cost: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=ModelUsageStatus.SUCCESS.value,
    )

    __table_args__ = (
        CheckConstraint("input_tokens >= 0", name="ck_model_usage_input_tokens_non_negative"),
        CheckConstraint("output_tokens >= 0", name="ck_model_usage_output_tokens_non_negative"),
        CheckConstraint("latency_ms >= 0", name="ck_model_usage_latency_non_negative"),
        CheckConstraint(
            f"status IN ('{ModelUsageStatus.SUCCESS.value}', '{ModelUsageStatus.ERROR.value}', '{ModelUsageStatus.RATE_LIMITED.value}')",
            name="ck_model_usage_status_valid",
        ),
        Index("ix_model_usage_workspace_created", "workspace_id", text("created_at DESC")),
        Index("ix_model_usage_request_id", "request_id"),
        Index("ix_model_usage_analysis", "analysis_id"),
    )

    # Relationships
    workspace: Mapped["Workspace | None"] = relationship("Workspace")
    analysis: Mapped["Analysis | None"] = relationship("Analysis")


class EvaluationRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "evaluation_runs"

    dataset_version: Mapped[str] = mapped_column(String(50), nullable=False)
    evaluator_version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=EvaluationRunStatus.QUEUED.value,
    )
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            f"status IN ('{EvaluationRunStatus.QUEUED.value}', '{EvaluationRunStatus.RUNNING.value}', '{EvaluationRunStatus.COMPLETED.value}', '{EvaluationRunStatus.PARTIAL.value}', '{EvaluationRunStatus.FAILED.value}', '{EvaluationRunStatus.CANCELLED.value}')",
            name="ck_evaluation_runs_status_valid",
        ),
        Index("ix_evaluation_runs_dataset_created", "dataset_version", text("created_at DESC")),
    )

    # Relationships
    cases: Mapped[list["EvaluationCase"]] = relationship(
        "EvaluationCase", back_populates="run", cascade="all, delete-orphan"
    )


class EvaluationCase(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "evaluation_cases"

    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[str] = mapped_column(String(100), nullable=False)
    actual_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    expected_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    scores: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=EvaluationCaseStatus.PASSED.value,
    )

    __table_args__ = (
        CheckConstraint(
            f"status IN ('{EvaluationCaseStatus.PASSED.value}', '{EvaluationCaseStatus.FAILED.value}', '{EvaluationCaseStatus.SKIPPED.value}', '{EvaluationCaseStatus.ERROR.value}')",
            name="ck_evaluation_cases_status_valid",
        ),
        UniqueConstraint("evaluation_run_id", "case_id", name="uq_evaluation_cases_run_case"),
        Index("ix_evaluation_cases_run", "evaluation_run_id"),
    )

    # Relationships
    run: Mapped["EvaluationRun"] = relationship("EvaluationRun", back_populates="cases")
