"""CLI for file-only extraction evaluation.

Example::

    python -m app.evaluation.runner --dataset app/evaluation/data/benchmark_v1.json \
      --predictions predictions.json --output report.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.evaluation.metrics import evaluate_dataset
from app.evaluation.models import BenchmarkDataset, PredictionFile


class EvaluationInputError(ValueError):
    """A deterministic, safe description of a malformed evaluation input file."""


def _load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationInputError(f"cannot load JSON from {path}: {type(exc).__name__}") from exc


def load_dataset(path: Path) -> BenchmarkDataset:
    try:
        return BenchmarkDataset.model_validate(_load_json(path))
    except ValidationError as exc:
        raise EvaluationInputError(f"invalid benchmark dataset: {len(exc.errors())} validation error(s)") from exc


def load_predictions(path: Path) -> PredictionFile:
    try:
        return PredictionFile.model_validate(_load_json(path))
    except ValidationError as exc:
        raise EvaluationInputError(f"invalid prediction file: {len(exc.errors())} validation error(s)") from exc


def render_summary(report: dict[str, Any]) -> str:
    def score(section: str) -> str:
        values = report[section]
        return f"P={values['precision']!s} R={values['recall']!s} F1={values['f1']:.3f}"

    coverage = report["coverage"]
    return "\n".join((
        f"Dataset: {report['dataset_version']} ({coverage['cases']} synthetic cases)",
        f"Model: {report['model_version']} | Prompt: {report['prompt_version']}",
        f"Facts: {score('facts')}",
        f"Requirements: {score('requirements')}",
        f"Languages: {coverage['languages']}",
    ))


def compare_reports(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Return candidate-minus-baseline deltas; promotion is intentionally not decided here."""
    paths = (
        ("facts", "f1"), ("requirements", "f1"),
        ("evidence_grounding", "exact_quote_match_rate"),
        ("attributes", "modality_accuracy"), ("attributes", "conditions_accuracy"),
        ("multilingual_integrity", "malayalam_source_preservation_rate"),
        ("quality", "duplicate_extraction_rate"), ("quality", "unsupported_metadata_inference_rate"),
    )
    deltas: dict[str, float | None] = {}
    for section, metric in paths:
        before, after = baseline.get(section, {}).get(metric), candidate.get(section, {}).get(metric)
        deltas[f"{section}.{metric}"] = None if before is None or after is None else after - before
    languages = sorted(set(baseline.get("by_language", {})) | set(candidate.get("by_language", {})))
    by_language = {language: {kind: (candidate.get("by_language", {}).get(language, {}).get(kind, {}).get("f1"), baseline.get("by_language", {}).get(language, {}).get(kind, {}).get("f1")) for kind in ("facts", "requirements")} for language in languages}
    return {"baseline_prompt_version": baseline.get("prompt_version"), "candidate_prompt_version": candidate.get("prompt_version"), "deltas": deltas, "by_language_f1": by_language, "automatic_promotion": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline Pramaan extraction evaluation")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare-to", type=Path, help="Existing JSON report for delta-only comparison")
    args = parser.parse_args(argv)
    try:
        report = evaluate_dataset(load_dataset(args.dataset), load_predictions(args.predictions))
        if args.compare_to:
            report["comparison"] = compare_reports(_load_json(args.compare_to), report)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (EvaluationInputError, ValueError) as exc:
        parser.error(str(exc))
    print(render_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
