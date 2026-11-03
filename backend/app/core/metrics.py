"""Low-cardinality operational metrics, emitted as structured log events.

Pramaan does not run Prometheus/Grafana. Each sample is one JSON log line::

    {"event": "metric", "metric_name": "extraction.runs", "metric_value": 1.0,
     "metric_unit": "count", "labels": {"status": "COMPLETED"}}

Cloud Monitoring log-based metrics match on ``jsonPayload.metric_name`` and extract
dimensions from ``jsonPayload.labels.<key>``; dashboards and alert policies are created in
GCP (see the deployment runbook), not by application code.

Cardinality guard: label *keys* are restricted to a fixed allowlist and label *values* are
short scalars. Workspace, user, document, chunk and run identifiers can therefore never
become metric dimensions even if a caller passes them; unknown labels are dropped.

Metrics must never break a request, so every emission is contained.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger("pramaan.metrics")

METRIC_EVENT = "metric"
MAX_LABEL_VALUE_LENGTH = 64

# Metric names. These strings are the contract with Cloud Monitoring filters.
METRIC_HTTP_REQUESTS = "api.requests"
METRIC_HTTP_DURATION_MS = "api.request.duration_ms"
METRIC_AUTH_UNAUTHENTICATED = "auth.unauthenticated"
METRIC_AUTHZ_FORBIDDEN = "authz.forbidden"
METRIC_DOCUMENT_UPLOADS = "documents.uploads"
METRIC_OCR_PAGES = "documents.ocr.pages"
METRIC_EXTRACTION_RUNS = "extraction.runs"
METRIC_EXTRACTION_DURATION_MS = "extraction.duration_ms"
METRIC_EXTRACTION_PAGES = "extraction.pages"
METRIC_SCHEMA_REJECTIONS = "extraction.schema_rejections"
METRIC_EVIDENCE_FAILURES = "extraction.evidence_failures"
METRIC_MODEL_REQUESTS = "model.requests"
METRIC_MODEL_TOKENS = "model.tokens"
METRIC_MODEL_LATENCY_MS = "model.latency_ms"
METRIC_DB_READINESS = "database.readiness"
METRIC_USAGE_LIMIT_REJECTIONS = "usage_limit.rejections"
METRIC_LANGFUSE_FAILURES = "langfuse.failures"

# Only bounded, non-identifying dimensions are allowed.
ALLOWED_LABEL_KEYS = frozenset(
    {
        "method",
        "route",
        "status_code",
        "status_class",
        "outcome",
        "status",
        "reason",
        "stage",
        "limit",
        "provider",
        "mode",
        "run_status",
        "page_status",
        "model_id",
        "direction",
    }
)

_lock = threading.Lock()
_samples: dict[str, float] = {}


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _clean_label_value(value: Any) -> str | int | bool | None:
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        return text[:MAX_LABEL_VALUE_LENGTH]
    return None


def sanitize_labels(labels: Mapping[str, Any] | None) -> dict[str, str | int | bool]:
    """Keep allowlisted label keys with short scalar values; drop everything else."""
    if not labels:
        return {}
    cleaned: dict[str, str | int | bool] = {}
    for key, value in labels.items():
        if not isinstance(key, str) or key not in ALLOWED_LABEL_KEYS:
            continue
        safe = _clean_label_value(value)
        if safe is not None:
            cleaned[key] = safe
    return cleaned


def format_sample_key(name: str, labels: Mapping[str, Any]) -> str:
    if not labels:
        return name
    parts = ",".join(f"{key}={labels[key]}" for key in sorted(labels))
    return f"{name}|{parts}"


def record_metric(
    name: str,
    value: float = 1.0,
    *,
    unit: str = "count",
    labels: Mapping[str, Any] | None = None,
) -> None:
    """Emit one metric sample. Never raises and never mutates caller state."""
    try:
        if not isinstance(name, str) or not name:
            return
        numeric = _as_number(value)
        if numeric is None:
            return
        clean_labels = sanitize_labels(labels)
        key = format_sample_key(name, clean_labels)
        with _lock:
            _samples[key] = _samples.get(key, 0.0) + numeric
        logger.info(
            METRIC_EVENT,
            extra={
                "event": METRIC_EVENT,
                "metric_name": name,
                "metric_value": numeric,
                "metric_unit": unit,
                "labels": clean_labels,
            },
        )
    except Exception:  # telemetry must never surface to callers
        return


def snapshot() -> dict[str, float]:
    """Aggregated samples recorded in this process (diagnostics and tests)."""
    with _lock:
        return dict(_samples)


def total(name: str, **labels: Any) -> float:
    """Aggregated value for one metric name plus optional label subset match."""
    with _lock:
        items = list(_samples.items())
    wanted = {k: str(v) for k, v in labels.items()}
    total_value = 0.0
    for key, value in items:
        if not key.startswith(f"{name}|") and key != name:
            continue
        if not wanted:
            total_value += value
            continue
        parsed = dict(part.split("=", 1) for part in key.split("|", 1)[1].split(",")) if "|" in key else {}
        if all(parsed.get(k) == v for k, v in wanted.items()):
            total_value += value
    return total_value


def reset() -> None:
    """Clear in-process aggregation (tests)."""
    with _lock:
        _samples.clear()
