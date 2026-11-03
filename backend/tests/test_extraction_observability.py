"""Observability integration: one trace per run, page spans, one generation per request.

DB-backed against a disposable pgvector database. Both external clients are fakes: the Meta
client is replaced before the extractor can build one, and the Langfuse client is injected
through the tracer, so no request leaves the machine.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.core import metrics, tracing
from app.core.config import get_settings
from app.ingestion.structured_extractor import StructuredExtractor
from app.models.audit_eval import ModelUsage
from app.models.fact_requirement import ExtractedFact, Requirement
from app.services.extraction_orchestrator import (
    RunStatus,
    run_document_extraction,
)
from tests.test_extraction_persistence import PAGE1, QUOTE, fact_payload, make_doc, req_payload
from tests.test_tracing import FakeClient

CHUNK = f"Intro paragraph. {QUOTE}"
TAIL = "Closing remarks."


@pytest.fixture(autouse=True)
def _isolate_telemetry():
    metrics.reset()
    tracing.override_tracer(tracing.Tracer(None))
    yield
    tracing.reset_tracer()
    metrics.reset()


@pytest.fixture
def doc(db_session, test_workspace):
    return make_doc(db_session, test_workspace, [(PAGE1, [CHUNK, TAIL])])


def _model_result(
    payload: dict, *, input_tokens=1171, output_tokens=5582, request_id="chatcmpl-1"
) -> SimpleNamespace:
    return SimpleNamespace(
        id=request_id,
        model="muse-spark-1.3",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=json.dumps(payload),
                    refusal=None,
                    tool_calls=None,
                    function_call=None,
                ),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=4473),
        ),
    )


class _FakeMetaClient:
    def __init__(self, result) -> None:
        self.calls = 0
        self._result = result
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **_kwargs):
        self.calls += 1
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _install(monkeypatch, tracer, meta_client) -> None:
    monkeypatch.setattr(tracing, "_tracer", tracer)
    monkeypatch.setattr("app.ingestion.structured_extractor.get_meta_client", lambda: meta_client)


def _run(db, ws, doc):
    return run_document_extraction(
        db, workspace_id=ws.id, document_id=doc.id, extractor=StructuredExtractor(get_settings())
    )


def _merged(observation) -> dict:
    merged: dict = dict(observation.metadata)
    for update in observation.updates:
        merged.update(update.get("metadata") or {})
    return merged


def _metadata_tree(observation) -> list[dict]:
    found = [observation.metadata, *[u.get("metadata") or {} for u in observation.updates]]
    for child in observation.children:
        found.extend(_metadata_tree(child))
    return found


def test_trace_structure_for_a_successful_run(db_session, test_workspace, doc, monkeypatch):
    client = FakeClient()
    meta = _FakeMetaClient(
        _model_result({"facts": [fact_payload()], "requirements": [req_payload()]})
    )
    _install(monkeypatch, tracing.Tracer(client, environment="test"), meta)

    result = _run(db_session, test_workspace, doc)

    assert result.status is RunStatus.COMPLETED
    assert meta.calls == 2, "one model request per canonical chunk"
    assert len(client.roots) == 1, "exactly one trace per extraction run"

    root = client.roots[0]
    assert root.metadata["extraction_run_id"] == result.extraction_run_id
    assert root.metadata["model_id"] == "muse-spark-1.3"
    assert root.metadata["prompt_version"] == "v1.2-structured-extraction"
    assert root.metadata["page_count"] == 1
    assert _merged(root)["status"] == "COMPLETED"
    assert _merged(root)["pages_processed"] == 1
    assert root.ended is True

    assert len(root.children) == 1, "one span per page"
    page = root.children[0]
    assert page.metadata["page_number"] == 1
    merged_page = _merged(page)
    assert merged_page["page_status"] == "PROCESSED"
    assert merged_page["model_calls"] == 2
    assert merged_page["facts_created"] == 1
    assert merged_page["requirements_created"] == 1
    assert page.ended is True

    generations = page.children
    assert len(generations) == meta.calls, "one generation per actual model request"
    for generation in generations:
        assert generation.as_type == "generation"
        assert generation.model == "muse-spark-1.3"
        assert generation.version == "v1.2-structured-extraction"
        update = generation.updates[-1]
        assert update["usage_details"] == {"input": 1171, "output": 5582}
        assert update["metadata"]["request_id"] == "chatcmpl-1"
        assert generation.ended is True

    assert client.flush_calls == 1


def test_generations_correlate_with_model_usage_without_duplicates(
    db_session, test_workspace, doc, monkeypatch
):
    client = FakeClient()
    meta = _FakeMetaClient(_model_result({"facts": [], "requirements": []}))
    _install(monkeypatch, tracing.Tracer(client), meta)

    result = _run(db_session, test_workspace, doc)

    rows = list(db_session.scalars(select(ModelUsage)).all())
    generations = client.roots[0].children[0].children
    assert len(rows) == len(generations) == 2, "tracing must not add usage rows"
    assert {row.request_id for row in rows} == {"chatcmpl-1"}
    assert {row.model_id for row in rows} == {"muse-spark-1.3"}
    assert result.model_requests_succeeded == 2


def test_trace_metadata_never_contains_document_text(
    db_session, test_workspace, doc, monkeypatch
):
    client = FakeClient()
    meta = _FakeMetaClient(
        _model_result({"facts": [fact_payload()], "requirements": [req_payload()]})
    )
    _install(monkeypatch, tracing.Tracer(client), meta)

    _run(db_session, test_workspace, doc)

    blob = json.dumps([_metadata_tree(root) for root in client.roots])
    assert QUOTE not in blob
    assert "Intro paragraph" not in blob
    assert "Closing remarks" not in blob


def test_schema_rejection_is_traced_but_never_persisted(
    db_session, test_workspace, doc, monkeypatch
):
    client = FakeClient()
    meta = _FakeMetaClient(
        _model_result(
            {
                "facts": [
                    {"fact_type": "NOT_A_FACT_TYPE", "raw_value": "x", "source_quote": QUOTE}
                ],
                "requirements": [],
            }
        )
    )
    _install(monkeypatch, tracing.Tracer(client), meta)

    result = _run(db_session, test_workspace, doc)

    assert result.status is RunStatus.FAILED
    assert result.pages[0].reason.value == "SCHEMA_VALIDATION_FAILED"
    generation = client.roots[0].children[0].children[0]
    assert generation.updates[-1]["level"] == "DEFAULT", "the HTTP request itself succeeded"
    assert _merged(client.roots[0].children[0])["reason"] == "SCHEMA_VALIDATION_FAILED"
    assert metrics.total(metrics.METRIC_SCHEMA_REJECTIONS) == 1
    assert db_session.scalar(select(func.count()).select_from(ExtractedFact)) == 0
    assert db_session.scalar(select(func.count()).select_from(Requirement)) == 0


def test_tracing_failure_leaves_extraction_intact(db_session, test_workspace, doc, monkeypatch):
    client = FakeClient(fail_start=True)  # every observation raises
    meta = _FakeMetaClient(
        _model_result({"facts": [fact_payload()], "requirements": [req_payload()]})
    )
    _install(monkeypatch, tracing.Tracer(client), meta)

    result = _run(db_session, test_workspace, doc)

    assert result.status is RunStatus.COMPLETED
    assert (result.facts_created, result.requirements_created) == (1, 1)
    assert db_session.scalar(select(func.count()).select_from(ExtractedFact)) == 1
    assert db_session.scalar(select(func.count()).select_from(ModelUsage)) == 2
    assert metrics.total(metrics.METRIC_LANGFUSE_FAILURES, stage="run_start") == 1


def test_inactive_tracing_changes_nothing(db_session, test_workspace, doc, monkeypatch):
    meta = _FakeMetaClient(
        _model_result({"facts": [fact_payload()], "requirements": [req_payload()]})
    )
    _install(monkeypatch, tracing.Tracer(None), meta)

    result = _run(db_session, test_workspace, doc)

    assert result.status is RunStatus.COMPLETED
    assert metrics.total(metrics.METRIC_LANGFUSE_FAILURES) == 0
    assert metrics.total(metrics.METRIC_EXTRACTION_RUNS, status="COMPLETED") == 1
    assert metrics.total(metrics.METRIC_EXTRACTION_PAGES, status="PROCESSED") == 1
    assert metrics.total(metrics.METRIC_MODEL_REQUESTS, outcome="success") == 2
    assert metrics.total(metrics.METRIC_MODEL_TOKENS, direction="input") == 2342


def test_evidence_verification_failures_are_counted_without_content(
    db_session, test_workspace, doc, monkeypatch
):
    meta = _FakeMetaClient(
        _model_result(
            {
                "facts": [fact_payload(quote="This sentence is not in the page at all.")],
                "requirements": [],
            }
        )
    )
    _install(monkeypatch, tracing.Tracer(None), meta)

    result = _run(db_session, test_workspace, doc)

    assert result.facts_created == 0
    assert result.proposals_skipped == 2  # both chunks propose the same unsupported quote
    assert metrics.total(metrics.METRIC_EVIDENCE_FAILURES, reason="QUOTE_NOT_IN_PAGE") == 2
