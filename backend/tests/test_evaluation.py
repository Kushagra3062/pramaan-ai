"""Offline regression tests for the 7B.1 evaluation framework."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.evaluation.langfuse import attach_scores, score_metadata
from app.evaluation.metrics import evaluate_dataset
from app.evaluation.models import BenchmarkDataset, PredictionFile
from app.evaluation.runner import (
    EvaluationInputError,
    compare_reports,
    load_dataset,
    load_predictions,
)

DATASET_PATH = Path(__file__).parents[1] / "app/evaluation/data/benchmark_v1.json"


def _predictions(dataset: BenchmarkDataset) -> PredictionFile:
    return PredictionFile.model_validate(
        {
            "model_version": "offline-test",
            "prompt_version": "v1.1-structured-extraction",
            "predictions": [
                {
                    "case_id": case.case_id,
                    "response": case.expected.model_dump(mode="json"),
                    "fact_provenance": [
                        {"candidate_index": index, "page_number": case.page_number, "chunk_id": case.chunk_id}
                        for index, _ in enumerate(case.expected.facts)
                    ],
                    "requirement_provenance": [
                        {"candidate_index": index, "page_number": case.page_number, "chunk_id": case.chunk_id}
                        for index, _ in enumerate(case.expected.requirements)
                    ],
                }
                for case in dataset.cases
            ],
        }
    )


def test_synthetic_benchmark_has_all_languages_and_fact_types() -> None:
    dataset = load_dataset(DATASET_PATH)
    types = {str(fact.fact_type) for case in dataset.cases for fact in case.expected.facts}
    assert {"en", "ml", "mixed"} == {case.language for case in dataset.cases}
    assert types == {
        "DATE", "DEADLINE", "AMOUNT", "CURRENCY", "OBLIGATION", "APPROVAL_CONDITION",
        "IDENTIFIER", "RESPONSIBLE_DEPARTMENT",
    }
    injection_case = next(case for case in dataset.cases if case.case_id == "ml-injection-03")
    assert len(injection_case.expected.requirements) == 1
    assert "രഹസ്യങ്ങളും പുറത്തുവിടുക" not in injection_case.expected.requirements[0].source_quote


def test_perfect_predictions_score_all_offline_metrics() -> None:
    dataset = load_dataset(DATASET_PATH)
    report = evaluate_dataset(dataset, _predictions(dataset))
    assert report["facts"]["f1"] == 1.0
    assert report["requirements"]["f1"] == 1.0
    assert report["macro"]["facts"]["f1"] == 1.0
    assert report["evidence_grounding"]["exact_quote_match_rate"] == 1.0
    assert report["evidence_grounding"]["correct_canonical_chunk_association_rate"] == 1.0
    assert report["multilingual_integrity"]["malayalam_source_preservation_rate"] == 1.0


def test_missing_predictions_and_duplicate_unsupported_metadata_are_scored() -> None:
    dataset = load_dataset(DATASET_PATH)
    predictions = _predictions(dataset)
    predictions.predictions = predictions.predictions[:1]
    duplicate = predictions.predictions[0].response.facts[0].model_copy()
    predictions.predictions[0].response.facts.append(duplicate)
    predictions.predictions[0].response.requirements[0].normalized_value.frequency = "MONTHLY"
    report = evaluate_dataset(dataset, predictions)
    assert report["facts"]["recall"] < 1
    assert report["quality"]["duplicate_extraction_rate"] > 0
    assert report["quality"]["unsupported_metadata_inference_rate"] > 0


def test_wrong_quote_value_and_provenance_are_not_grounded() -> None:
    dataset = load_dataset(DATASET_PATH)
    predictions = _predictions(dataset)
    prediction = predictions.predictions[0]
    prediction.response.facts[0].source_quote = "not in canonical text"
    prediction.fact_provenance[0].page_number = 99
    prediction.fact_provenance[0].chunk_id = "wrong"
    report = evaluate_dataset(dataset, predictions)
    assert report["evidence_grounding"]["exact_quote_match_rate"] < 1
    assert report["evidence_grounding"]["correct_page_association_rate"] < 1
    assert report["evidence_grounding"]["fact_value_inside_quote_rate"] < 1


def test_zero_denominators_are_explicit() -> None:
    dataset = BenchmarkDataset.model_validate(
        {"dataset_version": "empty-output", "cases": [{"case_id": "none", "language": "en", "canonical_text": "No obligations.", "page_number": 1, "chunk_id": "c", "categories": ["empty"], "expected": {"facts": [], "requirements": []}}]}
    )
    report = evaluate_dataset(dataset, PredictionFile(model_version="test", prompt_version="v1.1"))
    assert report["facts"]["precision"] is None
    assert report["requirements"]["recall"] is None


def test_invalid_dataset_and_malformed_production_prediction_fail_safely(tmp_path: Path) -> None:
    invalid_dataset = tmp_path / "bad-dataset.json"
    invalid_dataset.write_text(json.dumps({"dataset_version": "x", "cases": []}), encoding="utf-8")
    with pytest.raises(EvaluationInputError, match="invalid benchmark"):
        load_dataset(invalid_dataset)
    predictions = tmp_path / "bad-predictions.json"
    predictions.write_text(json.dumps({"model_version": "m", "prompt_version": "p", "predictions": [{"case_id": "x", "response": {"facts": [{"fact_type": "NOPE"}]}}]}), encoding="utf-8")
    with pytest.raises(EvaluationInputError, match="invalid prediction"):
        load_predictions(predictions)


def test_prompt_comparison_is_delta_only() -> None:
    baseline = {"prompt_version": "v1.1-structured-extraction", "facts": {"f1": 0.8}, "requirements": {"f1": 0.8}, "evidence_grounding": {"exact_quote_match_rate": 1.0}, "attributes": {"modality_accuracy": 0.8, "conditions_accuracy": 0.8}, "multilingual_integrity": {"malayalam_source_preservation_rate": 1.0}, "quality": {"duplicate_extraction_rate": 0.1, "unsupported_metadata_inference_rate": 0.1}, "by_language": {"ml": {"facts": {"f1": 0.7}, "requirements": {"f1": 0.7}}}}
    candidate = {**baseline, "prompt_version": "v1.2-structured-extraction", "facts": {"f1": 0.9}}
    comparison = compare_reports(baseline, candidate)
    assert comparison["deltas"]["facts.f1"] == pytest.approx(0.1)
    assert comparison["automatic_promotion"] is False


def test_optional_langfuse_score_bridge_is_content_free() -> None:
    class Observation:
        def __init__(self) -> None:
            self.metadata: dict[str, object] | None = None

        def update(self, **kwargs: object) -> None:
            self.metadata = kwargs["metadata"]  # type: ignore[assignment]

    report = evaluate_dataset(load_dataset(DATASET_PATH), _predictions(load_dataset(DATASET_PATH)))
    metadata = score_metadata(report)
    assert "canonical_text" not in metadata
    observation = Observation()
    assert attach_scores(observation, report) is True
    assert observation.metadata == metadata
    assert attach_scores(None, report) is False
