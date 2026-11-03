"""Opt-in, metadata-only bridge for attaching an offline score to an existing trace.

The CLI never imports or calls this module.  A caller must explicitly supply an already
created Langfuse observation from the existing tracing adapter; no credentials, network
client, source text, quotations, or ground truth are accepted here.
"""

from __future__ import annotations

from typing import Any, Protocol


class ScoreObservation(Protocol):
    def update(self, **kwargs: Any) -> Any: ...


def score_metadata(report: dict[str, Any]) -> dict[str, Any]:
    """Return an allowlisted, content-free score payload suitable for an injected trace."""
    return {
        "evaluation_dataset_version": str(report.get("dataset_version", ""))[:100],
        "evaluation_prompt_version": str(report.get("prompt_version", ""))[:100],
        "fact_f1": report.get("facts", {}).get("f1"),
        "requirement_f1": report.get("requirements", {}).get("f1"),
        "quote_match_rate": report.get("evidence_grounding", {}).get("exact_quote_match_rate"),
        "malayalam_preservation_rate": report.get("multilingual_integrity", {}).get(
            "malayalam_source_preservation_rate"
        ),
    }


def attach_scores(observation: ScoreObservation | None, report: dict[str, Any]) -> bool:
    """Attach only aggregate scores to an injected observation; never creates a client."""
    if observation is None:
        return False
    observation.update(metadata=score_metadata(report))
    return True
