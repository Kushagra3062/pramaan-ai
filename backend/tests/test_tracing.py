"""Offline tests for the metadata-only Langfuse tracing adapter.

Nothing here can reach Langfuse: the tracer is injected with a fake client that mimics the
SDK's observation API (``start_observation`` / ``update`` / ``end`` / ``flush`` / ``shutdown``).
"""

from __future__ import annotations

import logging

import pytest

from app.core import metrics, tracing
from app.core.config import get_settings


class FakeObservation:
    def __init__(self, *, name, as_type="span", metadata=None, model=None, version=None):
        self.name = name
        self.as_type = as_type
        self.metadata = metadata or {}
        self.model = model
        self.version = version
        self.updates: list[dict] = []
        self.children: list[FakeObservation] = []
        self.ended = False

    def start_observation(self, **kwargs):
        child = FakeObservation(**kwargs)
        self.children.append(child)
        return child

    def update(self, **kwargs):
        self.updates.append(kwargs)

    def end(self, **kwargs):
        self.ended = True


class ExplodingObservation(FakeObservation):
    """Every child observation and every update fails, as a broken exporter would."""

    def start_observation(self, **kwargs):
        raise RuntimeError("span export failed")

    def update(self, **kwargs):
        raise RuntimeError("span update failed")


class FakeClient:
    def __init__(self, *, fail_start=False, fail_flush=False):
        self.roots: list[FakeObservation] = []
        self.flush_calls = 0
        self.shutdown_calls = 0
        self.fail_start = fail_start
        self.fail_flush = fail_flush

    def start_observation(self, **kwargs):
        if self.fail_start:
            raise RuntimeError("langfuse unreachable")
        observation = FakeObservation(**kwargs)
        self.roots.append(observation)
        return observation

    def flush(self):
        self.flush_calls += 1
        if self.fail_flush:
            raise RuntimeError("flush failed")

    def shutdown(self):
        self.shutdown_calls += 1


@pytest.fixture(autouse=True)
def _isolate_tracer():
    """Never let a test build the real SDK client from ambient .env values."""
    metrics.reset()
    tracing.override_tracer(tracing.Tracer(None))
    yield
    tracing.reset_tracer()
    metrics.reset()


def _metadata_of(observation: FakeObservation) -> dict:
    merged: dict = dict(observation.metadata)
    for update in observation.updates:
        merged.update(update.get("metadata") or {})
    return merged


def _walk(observation: FakeObservation) -> list[FakeObservation]:
    found = [observation]
    for child in observation.children:
        found.extend(_walk(child))
    return found


# ── Architecture ─────────────────────────────────────────────────────────────


def test_one_trace_per_run_with_safe_metadata():
    client = FakeClient()
    tracer = tracing.Tracer(client, environment="test")
    run = tracer.start_run(
        metadata={
            "extraction_run_id": "run-1",
            "model_id": "muse-spark-1.3",
            "prompt_version": "v1.1-structured-extraction",
            "page_count": 2,
            "source_quote": "must not be forwarded",
        },
        version="v1.1-structured-extraction",
    )
    assert run.active is True
    assert len(client.roots) == 1
    root = client.roots[0]
    assert root.name == tracing.TRACE_NAME
    assert root.metadata["extraction_run_id"] == "run-1"
    assert root.metadata["model_id"] == "muse-spark-1.3"
    assert root.metadata["prompt_version"] == "v1.1-structured-extraction"
    assert root.metadata["page_count"] == 2
    assert "source_quote" not in root.metadata
    assert root.version == "v1.1-structured-extraction"


def test_one_span_per_page_under_the_run():
    client = FakeClient()
    run = tracing.Tracer(client).start_run(metadata={"extraction_run_id": "run-1"})
    page = run.page(1)
    page.finish(page_status="PROCESSED", reason=None, model_calls=2, facts_created=3)
    assert len(client.roots[0].children) == 1
    span = client.roots[0].children[0]
    assert span.name == tracing.PAGE_SPAN_NAME
    assert span.as_type == "span"
    assert span.metadata["page_number"] == 1
    assert _metadata_of(span)["page_status"] == "PROCESSED"
    assert _metadata_of(span)["facts_created"] == 3
    assert span.ended is True


def test_one_generation_per_model_request_with_token_attribution():
    client = FakeClient()
    run = tracing.Tracer(client).start_run(metadata={"extraction_run_id": "run-1"})
    page = run.page(1)
    token = page.activate()
    try:
        generation = tracing.open_generation(
            model_id="muse-spark-1.3", prompt_version="v1.1-structured-extraction"
        )
        generation.succeed(
            input_tokens=1171,
            output_tokens=5582,
            reasoning_tokens=4473,
            latency_ms=20400.0,
            request_id="chatcmpl-01a19660",
            finish_reason="stop",
        )
    finally:
        page.deactivate(token)

    child = client.roots[0].children[0].children[0]
    assert child.as_type == "generation"
    assert child.model == "muse-spark-1.3"
    assert child.version == "v1.1-structured-extraction"
    update = child.updates[-1]
    assert update["usage_details"] == {"input": 1171, "output": 5582}
    assert update["status_message"] == "success"
    assert update["level"] == "DEFAULT"
    assert update["metadata"]["request_id"] == "chatcmpl-01a19660"
    assert update["metadata"]["reasoning_tokens"] == 4473
    assert update["metadata"]["finish_reason"] == "stop"
    assert update["metadata"]["outcome"] == "success"
    # Never the request or the response body.
    assert "input" not in update
    assert "output" not in update
    assert child.ended is True


def test_generation_failure_marks_error_without_content():
    client = FakeClient()
    run = tracing.Tracer(client).start_run(metadata={"extraction_run_id": "run-1"})
    page = run.page(2)
    token = page.activate()
    try:
        generation = tracing.open_generation(model_id="muse-spark-1.3", prompt_version="v1")
        generation.fail("TIMEOUT")
    finally:
        page.deactivate(token)
    update = client.roots[0].children[0].children[0].updates[-1]
    assert update["level"] == "ERROR"
    assert update["status_message"] == "TIMEOUT"
    assert update["metadata"] == {"outcome": "failure", "error_category": "TIMEOUT"}


def test_generation_outside_a_page_is_a_noop():
    client = FakeClient()
    tracing.override_tracer(tracing.Tracer(client))
    generation = tracing.open_generation(model_id="muse-spark-1.3", prompt_version="v1")
    assert generation.active is False
    generation.succeed(input_tokens=1, output_tokens=2)
    generation.fail("TIMEOUT")
    generation.end()
    assert client.roots == []


def test_run_finish_flushes_and_ends_the_root():
    client = FakeClient()
    run = tracing.Tracer(client).start_run(metadata={"extraction_run_id": "run-1"})
    run.finish(status="COMPLETED", pages_processed=2, facts_created=10)
    root = client.roots[0]
    assert root.ended is True
    assert _metadata_of(root)["status"] == "COMPLETED"
    assert _metadata_of(root)["facts_created"] == 10
    assert client.flush_calls == 1


# ── Privacy ──────────────────────────────────────────────────────────────────


def test_attribute_allowlist_blocks_content():
    cleaned = tracing.sanitize_attributes(
        {
            "source_quote": "must not be forwarded",
            "text": "must not be forwarded",
            "raw_response": "{}",
            "note": "unknown key",
            "page_number": 2,
            "model_id": "muse-spark-1.3",
            "request_id": "z" * 500,
            "input": "payload",
            "output": "payload",
        }
    )
    assert cleaned == {
        "page_number": 2,
        "model_id": "muse-spark-1.3",
        "request_id": "z" * tracing.MAX_ATTRIBUTE_VALUE_LENGTH,
    }


def test_nested_structures_are_never_forwarded():
    cleaned = tracing.sanitize_attributes(
        {"page_count": 3, "facts_count": {"items": [1, 2]}, "reason": ["a"], "outcome": None}
    )
    assert cleaned == {"page_count": 3}


# ── Configuration and failure isolation ──────────────────────────────────────


def test_missing_credentials_leave_tracing_inactive(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")
    get_settings.cache_clear()
    tracing.reset_tracer()
    assert tracing.get_tracer().active is False


def test_disabled_flag_never_builds_a_client(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=tracing.__name__)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    get_settings.cache_clear()
    tracing.reset_tracer()
    assert tracing.get_tracer().active is False
    events = [r for r in caplog.records if getattr(r, "event", "") == "tracing.inactive"]
    assert events and events[-1].reason == "disabled_by_config"


def test_partial_credentials_leave_tracing_inactive(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=tracing.__name__)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    get_settings.cache_clear()
    tracing.reset_tracer()
    assert tracing.get_tracer().active is False
    events = [r for r in caplog.records if getattr(r, "event", "") == "tracing.inactive"]
    assert events and events[-1].reason == "partial_credentials"


def test_client_construction_failure_is_contained(monkeypatch):
    import langfuse

    def boom(**_kwargs):
        raise RuntimeError("cannot init")

    monkeypatch.setattr(langfuse, "Langfuse", boom)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    get_settings.cache_clear()
    tracing.reset_tracer()
    assert tracing.get_tracer().active is False
    assert metrics.total(metrics.METRIC_LANGFUSE_FAILURES, stage="init") == 1


def test_runtime_failure_disables_tracing_and_is_counted():
    client = FakeClient(fail_start=True)
    tracer = tracing.Tracer(client)
    run = tracer.start_run(metadata={"extraction_run_id": "run-1"})
    assert run.active is False
    assert tracer.active is False
    assert tracer.failures == 1
    assert metrics.total(metrics.METRIC_LANGFUSE_FAILURES, stage="run_start") == 1
    # Further work is a silent no-op: tracing is never retried.
    run.page(1).finish(page_status="PROCESSED")
    assert client.roots == []


def test_generation_start_failure_is_contained():
    tracer = tracing.Tracer(FakeClient())
    page = tracing.PageTrace(ExplodingObservation(name="page"), tracer)
    generation = page.generation(model_id="muse-spark-1.3", prompt_version="v1")
    assert generation.active is False
    assert tracer.active is False
    assert tracer.failures == 1
    assert metrics.total(metrics.METRIC_LANGFUSE_FAILURES, stage="generation_start") == 1


def test_page_finish_failure_is_contained():
    tracer = tracing.Tracer(FakeClient())
    page = tracing.PageTrace(ExplodingObservation(name="page"), tracer)
    page.finish(page_status="PROCESSED")  # must not raise
    assert tracer.failures == 1


def test_flush_failure_never_propagates():
    tracer = tracing.Tracer(FakeClient(fail_flush=True))
    run = tracer.start_run(metadata={"extraction_run_id": "run-1"})
    run.finish(status="COMPLETED")  # must not raise
    assert metrics.total(metrics.METRIC_LANGFUSE_FAILURES, stage="flush") == 1


def test_inactive_tracer_is_a_complete_noop():
    tracer = tracing.Tracer(None)
    run = tracer.start_run(metadata={"extraction_run_id": "run-1"})
    assert run.active is False
    page = run.page(1)
    assert page.active is False
    generation = page.generation(model_id="m", prompt_version="v")
    assert generation.active is False
    generation.succeed(input_tokens=1, output_tokens=2)
    generation.fail("TIMEOUT")
    generation.end()
    page.update(page_status="PROCESSED")
    page.finish(page_status="PROCESSED")
    run.finish(status="COMPLETED")
    tracer.flush()
    tracer.shutdown()
    assert tracer.failures == 0


def test_shutdown_flushes_and_deactivates():
    client = FakeClient()
    tracer = tracing.Tracer(client)
    tracer.start_run(metadata={"extraction_run_id": "run-1"}).finish(status="COMPLETED")
    tracer.shutdown()
    assert client.shutdown_calls == 1
    assert tracer.active is False


def test_shutdown_tracing_without_initialisation_is_a_noop():
    tracing.reset_tracer()
    tracing.shutdown_tracing()  # must not build a client just to shut it down


def test_get_tracer_is_cached():
    tracing.reset_tracer()
    assert tracing.get_tracer() is tracing.get_tracer()


def test_run_metadata_never_carries_content():
    client = FakeClient()
    run = tracing.Tracer(client).start_run(metadata={"extraction_run_id": "run-1"})
    run.page(1).finish(page_status="PROCESSED")
    run.finish(status="COMPLETED")
    blob = repr([_metadata_of(obs) for root in client.roots for obs in _walk(root)])
    assert "must not be forwarded" not in blob
