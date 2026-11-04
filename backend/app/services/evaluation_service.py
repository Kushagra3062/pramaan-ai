"""Benchmark execution with database persistence and Langfuse scores (FR-025).

Modes:
- ``mocked`` (default): grades caller-supplied predictions, or — when none are
  supplied — runs a labeled self-check that grades the dataset's own expected
  outputs. No model calls, no cost.
- ``live``: runs every benchmark case through the real StructuredExtractor
  (one budgeted model call per case), then grades. Explicitly warns about
  credit usage at the API layer.

Every run persists an EvaluationRun plus per-case EvaluationCase rows, writes
an audit event, finishes a Langfuse evaluation trace, and optionally pushes
aggregate scores via the Langfuse scores API. Nothing here auto-promotes a
prompt: comparison deltas are reported, never acted on.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.tracing import record_evaluation_scores, start_evaluation_trace
from app.db.repositories.audit_eval_repo import AuditRepository, EvaluationRepository
from app.evaluation.metrics import EVALUATOR_VERSION, evaluate_dataset
from app.evaluation.models import BenchmarkDataset, PredictionCase, PredictionFile
from app.models.enums import EvaluationCaseStatus, EvaluationRunStatus
from app.services import model_budget
from app.services.model_budget import ModelBudgetExceededError

logger = logging.getLogger(__name__)

BUNDLED_DATASET_PATH = Path(__file__).resolve().parent.parent / "evaluation" / "data" / "benchmark_v1.json"
SELF_CHECK_MODEL_VERSION = "self-check/baseline"


class EvaluationInputError(ValueError):
    """Safe, user-facing description of a rejected benchmark request."""


def load_bundled_dataset() -> BenchmarkDataset:
    try:
        payload = json.loads(BUNDLED_DATASET_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationInputError(f"bundled benchmark dataset is unreadable: {type(exc).__name__}") from exc
    try:
        return BenchmarkDataset.model_validate(payload)
    except ValidationError as exc:
        raise EvaluationInputError(f"bundled benchmark dataset is invalid: {len(exc.errors())} error(s)") from exc


def describe_datasets() -> list[dict[str, Any]]:
    dataset = load_bundled_dataset()
    return [{
        "dataset_version": dataset.dataset_version,
        "evaluator_version": EVALUATOR_VERSION,
        "case_count": len(dataset.cases),
        "case_ids": [case.case_id for case in dataset.cases],
        "languages": sorted({case.language for case in dataset.cases}),
    }]


def _self_check_predictions(dataset: BenchmarkDataset, prompt_version: str) -> PredictionFile:
    return PredictionFile(
        model_version=SELF_CHECK_MODEL_VERSION,
        prompt_version=prompt_version,
        predictions=[
            PredictionCase(case_id=case.case_id, response=case.expected) for case in dataset.cases
        ],
    )


def _live_predictions(
    dataset: BenchmarkDataset,
    settings: Settings,
    budget: model_budget.ModelBudget,
) -> tuple[PredictionFile, dict[str, str], int]:
    """Run each case through the real extractor. Per-case failures become ERROR cases."""
    from app.ingestion.structured_extractor import (
        STRUCTURED_EXTRACTION_PROMPT_VERSION,
        StructuredExtractor,
    )

    extractor = StructuredExtractor(settings)
    reservation = budget.reserve(len(dataset.cases))
    predictions: list[PredictionCase] = []
    errors: dict[str, str] = {}
    spent = 0
    try:
        for case in dataset.cases:
            reservation.consume()
            spent += 1
            try:
                result = extractor.extract_from_text(case.canonical_text)
            except Exception as exc:  # one bad case must not abort the benchmark
                errors[case.case_id] = type(exc).__name__
                logger.warning(
                    "evaluation_live_case_failed",
                    extra={"event": "evaluation.case_failed", "case_id": case.case_id,
                           "error_type": type(exc).__name__},
                )
                continue
            predictions.append(PredictionCase(case_id=case.case_id, response=result.response))
    finally:
        reservation.release_unused()
    prompt_version = STRUCTURED_EXTRACTION_PROMPT_VERSION
    return (
        PredictionFile(model_version=settings.meta_model_id, prompt_version=prompt_version, predictions=predictions),
        errors,
        spent,
    )


def run_benchmark(
    session: Session,
    *,
    mode: str = "mocked",
    predictions_payload: dict[str, Any] | None = None,
    prompt_version: str | None = None,
    push_langfuse_scores: bool = False,
    workspace_id: uuid.UUID | None = None,
    requested_by: uuid.UUID | None = None,
    request_id: str | None = None,
    settings: Settings,
    budget: model_budget.ModelBudget | None = None,
) -> tuple[uuid.UUID, dict[str, Any]]:
    """Execute a benchmark run, persist it, and return (run_id, report)."""
    if mode not in {"mocked", "live"}:
        raise EvaluationInputError("mode must be 'mocked' or 'live'")
    dataset = load_bundled_dataset()
    repo = EvaluationRepository(session)
    run = repo.create_run(dataset.dataset_version, EVALUATOR_VERSION, EvaluationRunStatus.RUNNING)
    session.flush()
    trace = start_evaluation_trace(
        run_id=str(run.id), dataset_version=dataset.dataset_version,
        evaluator_version=EVALUATOR_VERSION, mode=mode,
    )
    model_calls = 0
    live_errors: dict[str, str] = {}
    self_check = False
    try:
        if mode == "live":
            if not settings.model_api_key:
                raise EvaluationInputError("live evaluation needs MODEL_API_KEY")
            try:
                prediction_file, live_errors, model_calls = _live_predictions(
                    dataset, settings, budget or model_budget.get_model_budget()
                )
            except ModelBudgetExceededError as exc:
                raise EvaluationInputError("Daily model-call budget is exhausted. Try again tomorrow.") from exc
        elif predictions_payload is not None:
            try:
                prediction_file = PredictionFile.model_validate(predictions_payload)
            except ValidationError as exc:
                raise EvaluationInputError(f"invalid predictions payload: {len(exc.errors())} error(s)") from exc
        else:
            self_check = True
            prediction_file = _self_check_predictions(dataset, prompt_version or "self-check")
        report = evaluate_dataset(dataset, prediction_file)
    except EvaluationInputError:
        repo.update_run_status(run.id, EvaluationRunStatus.FAILED, metrics={"mode": mode, "model_calls": model_calls})
        trace.finish(status="FAILED", mode=mode, case_count=len(dataset.cases))
        raise
    report["mode"] = mode
    report["self_check"] = self_check
    report["model_calls"] = model_calls
    per_case = {entry["case_id"]: entry for entry in report.get("per_case", [])}
    passed = failed = errored = 0
    for case in dataset.cases:
        entry = per_case.get(case.case_id, {})
        if case.case_id in live_errors or entry.get("missing_prediction"):
            status = EvaluationCaseStatus.ERROR if case.case_id in live_errors else EvaluationCaseStatus.FAILED
        else:
            facts_f1 = (entry.get("facts") or {}).get("f1")
            reqs_f1 = (entry.get("requirements") or {}).get("f1")
            scores = [value for value in (facts_f1, reqs_f1) if isinstance(value, (int, float))]
            status = EvaluationCaseStatus.PASSED if scores and min(scores) >= 1.0 else EvaluationCaseStatus.FAILED
        if status == EvaluationCaseStatus.PASSED:
            passed += 1
        elif status == EvaluationCaseStatus.FAILED:
            failed += 1
        else:
            errored += 1
        prediction = next((item for item in prediction_file.predictions if item.case_id == case.case_id), None)
        repo.add_case(
            run.id, case.case_id,
            actual_output=prediction.response.model_dump(mode="json") if prediction else None,
            expected_output=case.expected.model_dump(mode="json"),
            scores=entry,
            status=status,
        )
    run_status = (
        EvaluationRunStatus.COMPLETED if not live_errors and failed == 0
        else EvaluationRunStatus.PARTIAL if passed > 0
        else EvaluationRunStatus.FAILED
    )
    summary_metrics = {
        "mode": mode, "self_check": self_check, "model_calls": model_calls,
        "fact_f1": (report.get("facts") or {}).get("f1"),
        "requirement_f1": (report.get("requirements") or {}).get("f1"),
        "quote_match_rate": (report.get("evidence_grounding") or {}).get("exact_quote_match_rate"),
        "passed": passed, "failed": failed, "errored": errored,
        "live_errors": live_errors,
    }
    repo.update_run_status(run.id, run_status, metrics=summary_metrics)
    AuditRepository(session).log(
        event_type="evaluation.run_completed", entity_type="evaluation_run",
        workspace_id=workspace_id, actor_user_id=requested_by, entity_id=run.id,
        request_id=request_id, metadata={**summary_metrics, "live_errors": len(live_errors)},
    )
    scores_pushed = False
    if push_langfuse_scores:
        scores_pushed = record_evaluation_scores(
            fact_f1=summary_metrics["fact_f1"],
            requirement_f1=summary_metrics["requirement_f1"],
            quote_match_rate=summary_metrics["quote_match_rate"],
        )
    trace.finish(
        status=run_status.value, mode=mode, case_count=len(dataset.cases),
        passed_cases=passed, failed_cases=failed, error_cases=errored,
        fact_f1=summary_metrics["fact_f1"], requirement_f1=summary_metrics["requirement_f1"],
        quote_match_rate=summary_metrics["quote_match_rate"],
    )
    report["run_id"] = str(run.id)
    report["run_status"] = run_status.value
    report["scores_pushed_to_langfuse"] = scores_pushed
    logger.info(
        "evaluation_run_completed",
        extra={"event": "evaluation.run_completed", "run_id": str(run.id),
               "status": run_status.value, "mode": mode},
    )
    return run.id, report
