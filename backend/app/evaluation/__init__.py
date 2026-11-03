"""Offline, deterministic extraction evaluation utilities.

This package deliberately has no database, model-provider, or telemetry dependency.
"""

from app.evaluation.metrics import EVALUATOR_VERSION, evaluate_dataset

__all__ = ["EVALUATOR_VERSION", "evaluate_dataset"]
