import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.repositories.base import BaseRepository
from app.models.audit_eval import AuditEvent, EvaluationCase, EvaluationRun, ModelUsage
from app.models.enums import EvaluationCaseStatus, EvaluationRunStatus, ModelUsageStatus


class AuditRepository(BaseRepository[AuditEvent]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, AuditEvent)

    def log(
        self,
        event_type: str,
        entity_type: str,
        workspace_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
        entity_id: uuid.UUID | None = None,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            request_id=request_id,
            metadata_=metadata or {},
        )
        return self.add(event)

    def list_by_workspace(
        self,
        workspace_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        stmt = (
            select(AuditEvent)
            .where(AuditEvent.workspace_id == workspace_id)
            .order_by(AuditEvent.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt).all())

    def list_by_request_id(self, request_id: str) -> list[AuditEvent]:
        stmt = (
            select(AuditEvent)
            .where(AuditEvent.request_id == request_id)
            .order_by(AuditEvent.created_at.asc())
        )
        return list(self.session.scalars(stmt).all())


class ModelUsageRepository(BaseRepository[ModelUsage]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, ModelUsage)

    def record(
        self,
        provider: str,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        workspace_id: uuid.UUID | None = None,
        analysis_id: uuid.UUID | None = None,
        request_id: str | None = None,
        estimated_cost: float | None = None,
        status: str | ModelUsageStatus = ModelUsageStatus.SUCCESS.value,
    ) -> ModelUsage:
        usage = ModelUsage(
            workspace_id=workspace_id,
            analysis_id=analysis_id,
            provider=provider,
            model_id=model_id,
            request_id=request_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            estimated_cost=estimated_cost,
            status=status.value if isinstance(status, ModelUsageStatus) else status,
        )
        return self.add(usage)

    def list_by_workspace(
        self,
        workspace_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ModelUsage]:
        stmt = (
            select(ModelUsage)
            .where(ModelUsage.workspace_id == workspace_id)
            .order_by(ModelUsage.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt).all())

    def list_by_analysis(self, analysis_id: uuid.UUID) -> list[ModelUsage]:
        stmt = select(ModelUsage).where(ModelUsage.analysis_id == analysis_id)
        return list(self.session.scalars(stmt).all())

    def get_total_tokens(self, workspace_id: uuid.UUID) -> dict[str, int]:
        stmt = select(
            func.coalesce(func.sum(ModelUsage.input_tokens), 0),
            func.coalesce(func.sum(ModelUsage.output_tokens), 0),
        ).where(ModelUsage.workspace_id == workspace_id)
        input_total, output_total = self.session.execute(stmt).one()
        return {"input_tokens": int(input_total), "output_tokens": int(output_total)}


class EvaluationRepository(BaseRepository[EvaluationRun]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, EvaluationRun)

    def create_run(
        self,
        dataset_version: str,
        evaluator_version: str,
        status: str | EvaluationRunStatus = EvaluationRunStatus.QUEUED.value,
    ) -> EvaluationRun:
        run = EvaluationRun(
            dataset_version=dataset_version,
            evaluator_version=evaluator_version,
            status=status.value if isinstance(status, EvaluationRunStatus) else status,
        )
        return self.add(run)

    def list_runs(
        self,
        dataset_version: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[EvaluationRun]:
        stmt = select(EvaluationRun)
        if dataset_version:
            stmt = stmt.where(EvaluationRun.dataset_version == dataset_version)
        stmt = stmt.order_by(EvaluationRun.created_at.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(stmt).all())

    def update_run_status(
        self,
        run_id: uuid.UUID,
        status: str | EvaluationRunStatus,
        metrics: dict[str, Any] | None = None,
    ) -> EvaluationRun | None:
        run = self.get_by_id(run_id)
        if run is not None:
            new_status = status.value if isinstance(status, EvaluationRunStatus) else status
            run.status = new_status
            if metrics is not None:
                run.metrics = metrics
            if new_status == EvaluationRunStatus.RUNNING.value and run.started_at is None:
                run.started_at = datetime.now(UTC)
            elif new_status in (
                EvaluationRunStatus.COMPLETED.value,
                EvaluationRunStatus.PARTIAL.value,
                EvaluationRunStatus.FAILED.value,
                EvaluationRunStatus.CANCELLED.value,
            ):
                run.completed_at = datetime.now(UTC)
            self.session.flush()
        return run

    def add_case(
        self,
        evaluation_run_id: uuid.UUID,
        case_id: str,
        actual_output: dict[str, Any] | None = None,
        expected_output: dict[str, Any] | None = None,
        scores: dict[str, Any] | None = None,
        status: str | EvaluationCaseStatus = EvaluationCaseStatus.PASSED.value,
    ) -> EvaluationCase:
        case = EvaluationCase(
            evaluation_run_id=evaluation_run_id,
            case_id=case_id,
            actual_output=actual_output,
            expected_output=expected_output,
            scores=scores,
            status=status.value if isinstance(status, EvaluationCaseStatus) else status,
        )
        self.session.add(case)
        self.session.flush()
        return case

    def get_cases(self, evaluation_run_id: uuid.UUID) -> list[EvaluationCase]:
        stmt = select(EvaluationCase).where(EvaluationCase.evaluation_run_id == evaluation_run_id)
        return list(self.session.scalars(stmt).all())
