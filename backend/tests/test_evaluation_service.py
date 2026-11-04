"""Database-backed tests for mocked benchmark runs (no model calls)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import EvaluationCaseStatus, EvaluationRunStatus
from app.models.workspace import Workspace
from app.services.evaluation_service import describe_datasets, run_benchmark


def test_describe_datasets_reports_bundled_version():
    datasets = describe_datasets()
    assert len(datasets) == 1
    assert datasets[0]["dataset_version"] == "7b.1-synthetic-v1"
    assert datasets[0]["case_count"] == 7


def test_mocked_self_check_completes_and_persists(db_session: Session, test_workspace: Workspace):
    run_id, report = run_benchmark(
        db_session, mode="mocked", workspace_id=test_workspace.id, settings=get_settings()
    )
    assert report["run_status"] == EvaluationRunStatus.COMPLETED.value
    assert report["self_check"] is True
    assert report["model_calls"] == 0
    assert report["facts"]["f1"] == 1.0
    assert report["requirements"]["f1"] == 1.0
    assert report["scores_pushed_to_langfuse"] is False
    assert str(run_id) == report["run_id"]


def test_mocked_run_with_empty_predictions_marks_failures(db_session: Session, test_workspace: Workspace):
    run_id, report = run_benchmark(
        db_session,
        mode="mocked",
        predictions_payload={"model_version": "test", "prompt_version": "test", "predictions": []},
        workspace_id=test_workspace.id,
        settings=get_settings(),
    )
    assert report["run_status"] == EvaluationRunStatus.FAILED.value
    assert report["facts"]["f1"] == 0.0


def test_mocked_run_rejects_unknown_case_ids(db_session: Session, test_workspace: Workspace):
    with pytest.raises(Exception, match="unknown case_id|invalid predictions"):
        run_benchmark(
            db_session,
            mode="mocked",
            predictions_payload={
                "model_version": "test",
                "prompt_version": "test",
                "predictions": [{"case_id": "nope", "response": {"facts": [], "requirements": []}}],
            },
            workspace_id=test_workspace.id,
            settings=get_settings(),
        )


def test_case_rows_cover_every_benchmark_case(db_session: Session, test_workspace: Workspace):
    from app.db.repositories.audit_eval_repo import EvaluationRepository

    run_id, _ = run_benchmark(db_session, mode="mocked", workspace_id=test_workspace.id, settings=get_settings())
    cases = EvaluationRepository(db_session).get_cases(run_id)
    assert len(cases) == 7
    assert all(case.status == EvaluationCaseStatus.PASSED.value for case in cases)
