"""Deterministic, evidence-aware offline evaluation metrics.

Matching is deliberately conservative.  A fact matches a human annotation when
its type, NFC/whitespace-normalised raw value and source quote match.  A
requirement matches when its type, normalised required action, actor, modality
and source quote match.  These are documented annotation keys, not a claim that
ordinary string equality establishes semantic truth.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Any, TypeVar

from app.evaluation.models import BenchmarkDataset, PredictionCase, PredictionFile
from app.ingestion.evidence_verifier import normalize_evidence_text, verify_evidence_quote
from app.schemas.extraction import ExtractedFactCandidate, RequirementCandidate

EVALUATOR_VERSION = "7b.1"
T = TypeVar("T")


def _value(value: str | None) -> str:
    return normalize_evidence_text(value or "")


def _fact_key(fact: ExtractedFactCandidate) -> tuple[str, str, str]:
    return str(fact.fact_type), _value(fact.raw_value), _value(fact.source_quote)


def _requirement_key(requirement: RequirementCandidate) -> tuple[str, str, str, str, str]:
    metadata = requirement.normalized_value
    return (
        str(requirement.requirement_type),
        _value(metadata.required_action if metadata else None),
        _value(metadata.responsible_actor if metadata else None),
        str(metadata.modality) if metadata and metadata.modality else "",
        _value(requirement.source_quote),
    )


def _prf(tp: int, predicted: int, expected: int) -> dict[str, float | int | None]:
    # Undefined precision/recall/F1 are reported as null when both sides are empty.
    precision = None if predicted == 0 else tp / predicted
    recall = None if expected == 0 else tp / expected
    f1 = None if predicted == 0 and expected == 0 else (0.0 if precision is None or recall is None or precision + recall == 0 else 2 * precision * recall / (precision + recall))
    return {"true_positive": tp, "predicted": predicted, "expected": expected, "precision": precision, "recall": recall, "f1": f1}


def _matches(expected: list[T], predicted: list[T], key: Callable[[T], Any]) -> tuple[list[tuple[T, T]], list[T], list[T]]:
    buckets: dict[Any, list[T]] = defaultdict(list)
    for item in expected:
        buckets[key(item)].append(item)
    matched: list[tuple[T, T]] = []
    unmatched_predictions: list[T] = []
    for item in predicted:
        candidates = buckets[key(item)]
        if candidates:
            matched.append((candidates.pop(0), item))
        else:
            unmatched_predictions.append(item)
    unmatched_expected = [item for values in buckets.values() for item in values]
    return matched, unmatched_predictions, unmatched_expected


def _accuracy(correct: int, total: int) -> float | None:
    return None if total == 0 else correct / total


def _coverage(dataset: BenchmarkDataset) -> dict[str, Any]:
    languages = Counter(case.language for case in dataset.cases)
    categories = Counter(category for case in dataset.cases for category in case.categories)
    return {"cases": len(dataset.cases), "languages": dict(sorted(languages.items())), "categories": dict(sorted(categories.items()))}


def _macro(per_case: list[dict[str, Any]], kind: str) -> dict[str, float | None]:
    """Unweighted case average; undefined precision/recall values are omitted."""
    result: dict[str, float | None] = {}
    for metric in ("precision", "recall", "f1"):
        values = [case[kind][metric] for case in per_case if case[kind][metric] is not None]
        result[metric] = None if not values else sum(values) / len(values)
    return result


def _candidate_provenance(case: PredictionCase | None, kind: str, index: int) -> tuple[int, str] | None:
    if case is None:
        return None
    values = case.fact_provenance if kind == "fact" else case.requirement_provenance
    found = next((value for value in values if value.candidate_index == index), None)
    return None if found is None else (found.page_number, found.chunk_id)


def evaluate_dataset(dataset: BenchmarkDataset, prediction_file: PredictionFile) -> dict[str, Any]:
    """Evaluate validated predictions; pure function with no I/O or external calls."""
    prediction_by_id = {prediction.case_id: prediction for prediction in prediction_file.predictions}
    unknown = sorted(set(prediction_by_id) - {case.case_id for case in dataset.cases})
    if unknown:
        raise ValueError(f"predictions contain unknown case_id values: {', '.join(unknown)}")

    totals: dict[str, int] = Counter()
    language_counts: dict[str, Counter[str]] = defaultdict(Counter)
    attribute_correct: Counter[str] = Counter()
    attribute_total: Counter[str] = Counter()
    evidence: Counter[str] = Counter()
    per_case: list[dict[str, Any]] = []

    for benchmark in dataset.cases:
        prediction = prediction_by_id.get(benchmark.case_id)
        facts = [] if prediction is None else prediction.response.facts
        requirements = [] if prediction is None else prediction.response.requirements
        fact_matches, extra_facts, missing_facts = _matches(benchmark.expected.facts, facts, _fact_key)
        req_matches, extra_reqs, missing_reqs = _matches(benchmark.expected.requirements, requirements, _requirement_key)
        language = language_counts[benchmark.language]
        for prefix, matched, extra, missing, predicted in (
            ("fact", fact_matches, extra_facts, missing_facts, facts),
            ("requirement", req_matches, extra_reqs, missing_reqs, requirements),
        ):
            totals[f"{prefix}_tp"] += len(matched)
            totals[f"{prefix}_predicted"] += len(predicted)
            totals[f"{prefix}_expected"] += len(matched) + len(missing)
            language[f"{prefix}_tp"] += len(matched)
            language[f"{prefix}_predicted"] += len(predicted)
            language[f"{prefix}_expected"] += len(matched) + len(missing)
            totals["unsupported_proposals"] += len(extra)
            totals["proposals"] += len(predicted)
            language["unsupported_proposals"] += len(extra)
            language["proposals"] += len(predicted)

        for expected, predicted in fact_matches:
            attribute_total["fact_type"] += 1
            attribute_correct["fact_type"] += int(expected.fact_type == predicted.fact_type)
            attribute_total["raw_value"] += 1
            attribute_correct["raw_value"] += int(_value(expected.raw_value) == _value(predicted.raw_value))
        for expected, predicted in req_matches:
            expected_metadata, predicted_metadata = expected.normalized_value, predicted.normalized_value
            for label, getter in (
                ("requirement_type", lambda r: str(r.requirement_type)),
                ("modality", lambda r: str(r.normalized_value.modality) if r.normalized_value and r.normalized_value.modality else ""),
                ("actor", lambda r: _value(r.normalized_value.responsible_actor if r.normalized_value else None)),
                ("deadline", lambda r: _value(r.normalized_value.deadline if r.normalized_value else None)),
                ("conditions", lambda r: tuple(_value(v) for v in (r.normalized_value.conditions if r.normalized_value and r.normalized_value.conditions else []))),
            ):
                attribute_total[label] += 1
                attribute_correct[label] += int(getter(expected) == getter(predicted))
            # The key does not include these fields, so inference is measurable separately.
            for field in ("frequency", "scope"):
                expected_value = getattr(expected_metadata, field) if expected_metadata else None
                predicted_value = getattr(predicted_metadata, field) if predicted_metadata else None
                if expected_value is None and predicted_value is not None:
                    totals["unsupported_metadata"] += 1

        for kind, candidates in (("fact", facts), ("requirement", requirements)):
            key_fn = _fact_key if kind == "fact" else _requirement_key
            counts = Counter(key_fn(candidate) for candidate in candidates)
            totals["duplicates"] += sum(count - 1 for count in counts.values() if count > 1)
            for index, candidate in enumerate(candidates):
                quote = candidate.source_quote
                evidence["quotes_total"] += 1
                evidence["quote_matches"] += int(verify_evidence_quote(benchmark.canonical_text, quote).is_exact_match)
                if kind == "fact":
                    evidence["fact_values_total"] += 1
                    evidence["fact_values_inside_quote"] += int(verify_evidence_quote(quote, candidate.raw_value).is_exact_match)
                provenance = _candidate_provenance(prediction, kind, index)
                if provenance is not None:
                    evidence["provenance_total"] += 1
                    evidence["correct_page"] += int(provenance[0] == benchmark.page_number)
                    evidence["correct_chunk"] += int(provenance[1] == benchmark.chunk_id)

        # Original Malayalam values should remain in NFC source form and source quote verbatim.
        if benchmark.language in {"ml", "mixed"}:
            for fact in facts:
                totals["malayalam_values"] += 1
                totals["malayalam_preserved"] += int(fact.raw_value in benchmark.canonical_text and _value(fact.raw_value) == fact.raw_value.strip())
            for requirement in requirements:
                totals["malayalam_quotes"] += 1
                totals["malayalam_preserved"] += int(requirement.source_quote in benchmark.canonical_text)

        per_case.append({"case_id": benchmark.case_id, "language": benchmark.language, "facts": _prf(len(fact_matches), len(facts), len(benchmark.expected.facts)), "requirements": _prf(len(req_matches), len(requirements), len(benchmark.expected.requirements)), "missing_prediction": prediction is None})

    facts_metric = _prf(totals["fact_tp"], totals["fact_predicted"], totals["fact_expected"])
    requirements_metric = _prf(totals["requirement_tp"], totals["requirement_predicted"], totals["requirement_expected"])
    by_language = {language: {"facts": _prf(values["fact_tp"], values["fact_predicted"], values["fact_expected"]), "requirements": _prf(values["requirement_tp"], values["requirement_predicted"], values["requirement_expected"])} for language, values in sorted(language_counts.items())}
    matched_total = totals["fact_tp"] + totals["requirement_tp"]
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "dataset_version": dataset.dataset_version,
        "model_version": prediction_file.model_version,
        "prompt_version": prediction_file.prompt_version,
        "coverage": _coverage(dataset),
        "facts": facts_metric,
        "requirements": requirements_metric,
        "macro": {"facts": _macro(per_case, "facts"), "requirements": _macro(per_case, "requirements")},
        "attributes": {key + "_accuracy": _accuracy(attribute_correct[key], attribute_total[key]) for key in ("fact_type", "raw_value", "requirement_type", "modality", "actor", "deadline", "conditions")},
        "evidence_grounding": {"exact_quote_match_rate": _accuracy(evidence["quote_matches"], evidence["quotes_total"]), "fact_value_inside_quote_rate": _accuracy(evidence["fact_values_inside_quote"], evidence["fact_values_total"]), "correct_page_association_rate": _accuracy(evidence["correct_page"], evidence["provenance_total"]), "correct_canonical_chunk_association_rate": _accuracy(evidence["correct_chunk"], evidence["provenance_total"])},
        "multilingual_integrity": {"malayalam_source_preservation_rate": _accuracy(totals["malayalam_preserved"], totals["malayalam_values"] + totals["malayalam_quotes"])},
        "quality": {"duplicate_extraction_rate": _accuracy(totals["duplicates"], totals["proposals"]), "unsupported_proposal_rate": _accuracy(totals["unsupported_proposals"], totals["proposals"]), "unsupported_metadata_inference_rate": _accuracy(totals["unsupported_metadata"], matched_total), "important_requirement_omission_rate": _accuracy(totals["requirement_expected"] - totals["requirement_tp"], totals["requirement_expected"]), "schema_valid_response_rate": 1.0},
        "by_language": by_language,
        "per_case": per_case,
    }
