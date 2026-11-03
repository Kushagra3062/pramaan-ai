"""Metric emission, cardinality guard and Cloud Monitoring log-shape tests."""

from __future__ import annotations

import logging

import pytest

from app.core import metrics


@pytest.fixture(autouse=True)
def _clean_metrics():
    metrics.reset()
    yield
    metrics.reset()


def test_records_sample_with_sorted_labels():
    metrics.record_metric(
        metrics.METRIC_EXTRACTION_RUNS, labels={"status": "PARTIAL"}
    )
    snap = metrics.snapshot()
    assert snap == {"extraction.runs|status=PARTIAL": 1.0}
    metrics.record_metric(metrics.METRIC_EXTRACTION_RUNS, labels={"status": "PARTIAL"})
    assert metrics.total(metrics.METRIC_EXTRACTION_RUNS, status="PARTIAL") == 2.0


def test_identifiers_cannot_become_metric_dimensions():
    metrics.record_metric(
        metrics.METRIC_EXTRACTION_PAGES,
        labels={
            "status": "PROCESSED",
            "workspace_id": "d4e5436a-d789-44a6-87b5-24459d90d33e",
            "document_id": "d5ba49fd-dc45-4303-abc2-882504888066",
            "user_id": "user-1",
            "extraction_run_id": "3f81fd79-f188-422c-b09d-5009c8fdf913",
        },
    )
    key = next(iter(metrics.snapshot()))
    assert key == "extraction.pages|status=PROCESSED"
    assert "id" not in key


def test_non_numeric_values_are_ignored():
    metrics.record_metric(metrics.METRIC_MODEL_TOKENS, value="1171")
    metrics.record_metric(metrics.METRIC_MODEL_TOKENS, value=None)
    metrics.record_metric(metrics.METRIC_MODEL_TOKENS, value=True)
    assert metrics.snapshot() == {}


def test_label_values_must_be_scalars():
    metrics.record_metric(
        metrics.METRIC_HTTP_REQUESTS,
        labels={"method": "GET", "route": {"nested": "value"}, "status_class": None},
    )
    assert metrics.snapshot() == {"api.requests|method=GET": 1.0}


def test_non_allowlisted_and_empty_labels_are_dropped():
    metrics.record_metric(
        metrics.METRIC_DOCUMENT_UPLOADS,
        labels={"outcome": "success", "note": "not a dimension", "reason": ""},
    )
    assert metrics.snapshot() == {"documents.uploads|outcome=success": 1.0}


def test_metric_line_has_cloud_logging_shape(caplog):
    caplog.set_level(logging.INFO, logger="pramaan.metrics")
    metrics.record_metric(
        metrics.METRIC_MODEL_LATENCY_MS, value=48.1, unit="ms", labels={"model_id": "muse-spark-1.3"}
    )
    record = next(r for r in caplog.records if getattr(r, "event", "") == "metric")
    assert record.metric_name == "model.latency_ms"
    assert record.metric_value == 48.1
    assert record.metric_unit == "ms"
    assert record.labels == {"model_id": "muse-spark-1.3"}


def test_emission_never_raises():
    metrics.record_metric(None)  # type: ignore[arg-type]
    metrics.record_metric("")
    metrics.record_metric(metrics.METRIC_OCR_PAGES, value=object(), labels={"route": object()})
    metrics.record_metric(metrics.METRIC_OCR_PAGES, value=None, labels={"route": ["unhashable"]})
    # Only the last call is a valid sample: unhashable label values are dropped, not fatal.
    metrics.record_metric(metrics.METRIC_OCR_PAGES, labels={"route": ["unhashable"]})
    assert metrics.snapshot() == {"documents.ocr.pages": 1.0}


def test_total_without_labels_sums_every_sample():
    metrics.record_metric(metrics.METRIC_EXTRACTION_RUNS, labels={"status": "COMPLETED"})
    metrics.record_metric(metrics.METRIC_EXTRACTION_RUNS, labels={"status": "FAILED"})
    assert metrics.total(metrics.METRIC_EXTRACTION_RUNS) == 2.0
    assert metrics.total(metrics.METRIC_EXTRACTION_RUNS, status="FAILED") == 1.0
