"""Evaluation console API: datasets, benchmark runs, failure explorer (FR-024/FR-025).

Mocked mode is free and default. Live mode spends one budgeted model call per
benchmark case and warns the caller through the response metadata; the UI must
surface that warning before submitting.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.auth import (
    AuthenticatedUser,
    get_authenticated_user,
    require_workspace_read,
    require_workspace_write,
)
from app.core.config import Settings, get_settings
from app.db.engine import get_db
from app.db.repositories.audit_eval_repo import EvaluationRepository
from app.schemas.evaluation_api import (
    EvaluationCaseResponse,
    EvaluationDatasetResponse,
    EvaluationRunDetailResponse,
    EvaluationRunRequest,
    EvaluationRunResponse,
    EvaluationRunSummary,
    PaginatedEvaluationRunsResponse,
)
from app.services.evaluation_service import (
    EvaluationInputError,
    describe_datasets,
    run_benchmark,
)

router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/evaluation",
    tags=["evaluation"],
    dependencies=[Depends(require_workspace_read)],
)


@router.get("/datasets", response_model=list[EvaluationDatasetResponse])
def list_datasets() -> list[EvaluationDatasetResponse]:
    try:
        return [EvaluationDatasetResponse(**item) for item in describe_datasets()]
    except EvaluationInputError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.get("/runs", response_model=PaginatedEvaluationRunsResponse)
def list_runs(
    workspace_id: uuid.UUID,
    session: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PaginatedEvaluationRunsResponse:
    del workspace_id  # runs are installation-scoped; membership is checked by the router guard.
    items = EvaluationRepository(session).list_runs(limit=limit + 1, offset=offset)
    total = offset + len(items)
    page = items[:limit]
    return PaginatedEvaluationRunsResponse(
        items=[
            EvaluationRunSummary(
                id=item.id, dataset_version=item.dataset_version, evaluator_version=item.evaluator_version,
                status=item.status, metrics=dict(item.metrics or {}) if item.metrics else None,
                created_at=item.created_at, completed_at=item.completed_at,
            )
            for item in page
        ],
        total=total if len(items) <= limit else total + 1,
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}", response_model=EvaluationRunDetailResponse)
def get_run(
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    session: Session = Depends(get_db),
) -> EvaluationRunDetailResponse:
    del workspace_id
    repo = EvaluationRepository(session)
    run = repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Evaluation run not found.")
    cases = [
        EvaluationCaseResponse(
            case_id=item.case_id, status=item.status,
            scores=dict(item.scores or {}) if item.scores else None,
            expected_output=dict(item.expected_output or {}) if item.expected_output else None,
            actual_output=dict(item.actual_output or {}) if item.actual_output else None,
        )
        for item in repo.get_cases(run.id)
    ]
    return EvaluationRunDetailResponse(
        id=run.id, dataset_version=run.dataset_version, evaluator_version=run.evaluator_version,
        status=run.status, metrics=dict(run.metrics or {}) if run.metrics else None,
        cases=cases, created_at=run.created_at, completed_at=run.completed_at,
    )


@router.post("/runs", response_model=EvaluationRunResponse, dependencies=[Depends(require_workspace_write)])
def start_run(
    workspace_id: uuid.UUID,
    body: EvaluationRunRequest,
    request: Request,
    session: Session = Depends(get_db),
    principal: AuthenticatedUser = Depends(get_authenticated_user),
    settings: Settings = Depends(get_settings),
) -> EvaluationRunResponse:
    try:
        run_id, report = run_benchmark(
            session, mode=body.mode, predictions_payload=body.predictions,
            prompt_version=body.prompt_version, push_langfuse_scores=body.push_langfuse_scores,
            workspace_id=workspace_id, requested_by=principal.user.id,
            request_id=request.headers.get("X-Request-ID"), settings=settings,
        )
    except EvaluationInputError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    session.commit()
    return EvaluationRunResponse(
        run_id=run_id, run_status=str(report.pop("run_status")),
        mode=str(report.get("mode", body.mode)), dataset_version=str(report.get("dataset_version", "")),
        evaluator_version=str(report.get("evaluator_version", "")), report=report,
    )
