"""Safe Pydantic schema-rejection diagnostics (Milestone 4B.5C.5C.1). Fully offline.

The diagnostics may contain field locations, Pydantic error types and counts. They must never
contain rejected values, error messages (which can echo values), the raw response or source text.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.ingestion.structured_extractor import (
    MAX_REPORTED_SCHEMA_ERRORS,
    ExtractionRefusalError,
    ExtractionTruncatedError,
    ExtractionUnexpectedFinishReasonError,
    ExtractionUnexpectedToolCallsError,
    ExtractionValidationError,
    ResponseDiagnostics,
    SchemaErrorDiagnostics,
    StructuredExtractor,
    _safe_loc_part,
    summarize_validation_error,
)
from app.schemas.extraction import StructuredExtractionResponse
from tests.test_extraction_orchestrator import FakeExtractor
from tests.test_extraction_persistence import PAGE1, QUOTE, make_doc

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "diagnose_meta_extraction.py"
_spec = importlib.util.spec_from_file_location("diagnose_meta_extraction", _SCRIPT)
script = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = script
_spec.loader.exec_module(script)

SECRET_VALUE = "SECRET-REJECTED-VALUE-123"
SECRET_EXTRA = "SECRET-EXTRA-VALUE-456"
SECRET_RESPONSE = "SECRET-RAW-RESPONSE-789"
SECRET_SOURCE = "SECRET-SOURCE-TEXT-321"
API_KEY = "sk-test-SECRET-KEY-654"
GOOD_FACT = {"fact_type": "DATE", "raw_value": "1 Nov", "source_quote": "On 1 Nov."}


def validation_error(payload: dict) -> ValidationError:
    with pytest.raises(ValidationError) as info:
        StructuredExtractionResponse.model_validate(payload)
    return info.value


def locs(summary: SchemaErrorDiagnostics) -> list[tuple]:
    return [(e.loc, e.type) for e in summary.errors]


# 1-6: serializer
def test_enum_error():
    summary = summarize_validation_error(
        validation_error({"facts": [{**GOOD_FACT, "fact_type": SECRET_VALUE}], "requirements": []})
    )
    assert summary.error_count == 1 and not summary.truncated_error_list
    assert locs(summary) == [(("facts", 0, "fact_type"), "enum")]


def test_missing_required_field():
    bad = {"fact_type": "DATE", "source_quote": "q"}
    summary = summarize_validation_error(validation_error({"facts": [bad], "requirements": []}))
    assert locs(summary) == [(("facts", 0, "raw_value"), "missing")]


def test_unexpected_extra_field():
    summary = summarize_validation_error(
        validation_error({"facts": [{**GOOD_FACT, "bogus": SECRET_EXTRA}], "requirements": []})
    )
    assert locs(summary) == [(("facts", 0, "bogus"), "extra_forbidden")]  # the field name, never its value


def test_multiple_errors_with_nested_list_indexes():
    payload = {
        "facts": [GOOD_FACT, {"fact_type": "DATE", "source_quote": "q"}],
        "requirements": [
            {
                "title": "t", "description": "d", "source_quote": "q", "requirement_type": "REPORTING",
                "normalized_value": {"modality": SECRET_VALUE},
            }
        ],
    }
    summary = summarize_validation_error(validation_error(payload))
    assert summary.error_count == 2
    by_loc = dict(locs(summary))
    assert by_loc[("facts", 1, "raw_value")] == "missing"
    assert by_loc[("requirements", 0, "normalized_value", "modality")] == "value_error"
    first = next(e for e in summary.errors if e.loc[0] == "facts")
    assert isinstance(first.loc[1], int) and first.loc[1] == 1  # list index stays an int
    assert first.path == "facts.1.raw_value"


def test_error_list_is_capped_but_count_is_exact():
    payload = {"facts": [{"fact_type": "DATE", "source_quote": "q"} for _ in range(25)], "requirements": []}
    summary = summarize_validation_error(validation_error(payload))
    assert summary.error_count == 25 and len(summary.errors) == MAX_REPORTED_SCHEMA_ERRORS == 20
    assert summary.truncated_error_list is True
    small = summarize_validation_error(validation_error(payload), max_errors=3)
    assert len(small.errors) == 3 and small.truncated_error_list is True


def test_as_dict_matches_documented_structure():
    summary = summarize_validation_error(
        validation_error({"facts": [{**GOOD_FACT, "fact_type": SECRET_VALUE}], "requirements": []})
    )
    assert summary.as_dict() == {
        "error_count": 1,
        "errors": [{"loc": ["facts", 0, "fact_type"], "type": "enum"}],
        "truncated_error_list": False,
    }
    json.dumps(summary.as_dict())  # JSON-serialisable for the structured logger


def test_unexpected_loc_parts_are_represented_safely():
    assert _safe_loc_part(3) == 3 and _safe_loc_part("field") == "field"
    assert _safe_loc_part(object()) == "<object>" and _safe_loc_part(None) == "<NoneType>"
    assert _safe_loc_part(True) == "<bool>"
    assert _safe_loc_part("x" * 200).endswith("…") and len(_safe_loc_part("x" * 200)) == 65
    assert _safe_loc_part("a\nb\x00c") == "a?b?c"


# 7: rejected values never appear
def test_rejected_values_not_in_serialized_metadata():
    payload = {
        "facts": [{"fact_type": SECRET_VALUE, "raw_value": 12345, "source_quote": SECRET_SOURCE, "x": SECRET_EXTRA}],
        "requirements": [
            {"title": "t", "description": "d", "source_quote": "q",
             "normalized_value": {"modality": SECRET_VALUE}}
        ],
    }
    exc = validation_error(payload)
    assert SECRET_VALUE in str(exc)  # sanity: pydantic's own text DOES echo the rejected value
    summary = summarize_validation_error(exc)
    text = json.dumps(summary.as_dict(), ensure_ascii=False) + repr(summary)
    for secret in (SECRET_VALUE, SECRET_SOURCE, SECRET_EXTRA, "12345", "Invalid modality"):
        assert secret not in text


# 8-9: extractor behaviour and logging
def _response(content, finish="stop", usage=(1171, 6623, 6000)):
    message = SimpleNamespace(content=content, refusal=None, tool_calls=None, function_call=None)
    usage_obj = SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1],
                                completion_tokens_details=SimpleNamespace(reasoning_tokens=usage[2]))
    return SimpleNamespace(id="chatcmpl-schema-1", model="muse-spark-1.3", usage=usage_obj,
                           choices=[SimpleNamespace(message=message, finish_reason=finish)])


def _extract(monkeypatch, content):
    client = MagicMock()
    client.chat.completions.create.return_value = _response(content)
    monkeypatch.setattr("app.ingestion.structured_extractor.get_meta_client", lambda: client)
    return StructuredExtractor(Settings(model_api_key=API_KEY)).extract_from_text(SECRET_SOURCE)


BAD_CONTENT = json.dumps({
    "facts": [{"fact_type": SECRET_VALUE, "raw_value": SECRET_RESPONSE, "source_quote": "q"}],
    "requirements": [{"title": "t", "description": "d", "source_quote": "q",
                      "normalized_value": {"modality": SECRET_VALUE}}],
})


def test_extractor_still_raises_the_same_typed_failure_with_schema_errors(monkeypatch):
    with pytest.raises(ExtractionValidationError) as info:
        _extract(monkeypatch, BAD_CONTENT)
    exc = info.value
    assert exc.category == "SCHEMA_VALIDATION_FAILED"
    assert exc.schema_errors.error_count == 2
    assert dict(locs(exc.schema_errors)) == {
        ("facts", 0, "fact_type"): "enum",
        ("requirements", 0, "normalized_value", "modality"): "value_error",
    }
    d = exc.diagnostics  # request id / tokens / finish reason preserved
    assert (d.request_id, d.finish_reason, d.input_tokens, d.output_tokens, d.reasoning_tokens) == (
        "chatcmpl-schema-1", "stop", 1171, 6623, 6000)
    for secret in (SECRET_VALUE, SECRET_RESPONSE, SECRET_SOURCE, API_KEY):
        assert secret not in str(exc)


def test_rejection_log_has_sanitized_schema_fields_and_no_values(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ExtractionValidationError):
        _extract(monkeypatch, BAD_CONTENT)
    rec = next(r for r in caplog.records if getattr(r, "event", "") == "extraction.response_rejected")
    assert rec.error_category == "SCHEMA_VALIDATION_FAILED" and rec.schema_error_count == 2
    assert {"loc": ["facts", 0, "fact_type"], "type": "enum"} in rec.schema_errors
    assert rec.schema_errors_truncated is False
    assert (rec.request_id, rec.finish_reason, rec.input_tokens, rec.output_tokens, rec.reasoning_tokens) == (
        "chatcmpl-schema-1", "stop", 1171, 6623, 6000)
    assert rec.latency_ms >= 0 and rec.content_length == len(BAD_CONTENT)
    everything = "\n".join(r.getMessage() + repr(r.__dict__) for r in caplog.records)
    for secret in (SECRET_VALUE, SECRET_RESPONSE, SECRET_SOURCE, API_KEY, "Invalid modality"):
        assert secret not in everything


def test_non_object_json_is_schema_failure_without_pydantic_details(monkeypatch):
    with pytest.raises(ExtractionValidationError) as info:
        _extract(monkeypatch, json.dumps([SECRET_RESPONSE]))
    assert info.value.schema_errors is None and SECRET_RESPONSE not in str(info.value)


def test_orchestrator_logs_sanitized_schema_errors_and_keeps_reason(db_session, test_workspace, caplog):
    from app.services.extraction_orchestrator import PageReason, run_document_extraction

    caplog.set_level(logging.WARNING)
    doc = make_doc(db_session, test_workspace, [(PAGE1, [f"Intro paragraph. {QUOTE}"])])
    schema = summarize_validation_error(
        validation_error({"facts": [{**GOOD_FACT, "fact_type": SECRET_VALUE}], "requirements": []}))
    exc = ExtractionValidationError("x", ResponseDiagnostics(request_id="r1", finish_reason="stop",
                                                              input_tokens=10, output_tokens=20), schema)
    result = run_document_extraction(db_session, workspace_id=test_workspace.id, document_id=doc.id,
                                     extractor=FakeExtractor({f"Intro paragraph. {QUOTE}": exc}))
    assert result.pages[0].reason is PageReason.SCHEMA_VALIDATION_FAILED  # outcome unchanged
    rec = next(r for r in caplog.records if getattr(r, "event", "") == "extraction.chunk_failed")
    assert rec.schema_error_count == 1 and rec.schema_errors == [{"loc": ["facts", 0, "fact_type"], "type": "enum"}]
    assert SECRET_VALUE not in "\n".join(repr(r.__dict__) for r in caplog.records)


# 10-11, 13: diagnostic script
@pytest.fixture
def doc(db_session, test_workspace):
    return make_doc(db_session, test_workspace, [(PAGE1, [f"{SECRET_SOURCE} {QUOTE}"])])


def _run_script(db_session, doc, extractor, capsys):
    from app.db.repositories.document_repo import DocumentChunkRepository

    chunk = DocumentChunkRepository(db_session).list_by_document(doc.id)[0]
    code = script.main(
        ["--execute", "--document-id", str(doc.id), "--page-number", "1", "--chunk-id", str(chunk.id)],
        env={"RUN_META_DIAGNOSTIC": "true"}, session_factory=lambda: db_session, extractor_factory=lambda: extractor)
    return code, capsys.readouterr().out


def _schema_exc(schema, message="x"):
    d = ResponseDiagnostics(request_id="chatcmpl-9", response_model="muse-spark-1.3", finish_reason="stop",
                            input_tokens=1171, output_tokens=6623, reasoning_tokens=6000, content_length=4000, choices_count=1)
    return ExtractionValidationError(message, d, schema)


def test_script_prints_safe_validation_metadata(db_session, doc, capsys):
    schema = summarize_validation_error(validation_error({
        "facts": [{**GOOD_FACT, "fact_type": SECRET_VALUE}],
        "requirements": [{"title": "t", "description": "d", "source_quote": "q", "normalized_value": {"modality": SECRET_VALUE}}]}))
    exc = _schema_exc(schema, message=f"leaky message {SECRET_VALUE}")
    code, out = _run_script(db_session, doc, FakeExtractor({f"{SECRET_SOURCE} {QUOTE}": exc}), capsys)
    assert code == script.EXIT_REQUEST_FAILED
    for expected in ("error_category: SCHEMA_VALIDATION_FAILED", "request_id: chatcmpl-9", "response_model: muse-spark-1.3",
                     "finish_reason: stop", "input_tokens: 1171", "output_tokens: 6623", "reasoning_tokens: 6000",
                     "json_parsed: True", "validation_error_count: 2", "validation_errors: 2 listed",
                     "facts.0.fact_type (enum)", "requirements.0.normalized_value.modality (value_error)",
                     "validation_errors_truncated: False"):
        assert expected in out, expected
    for secret in (SECRET_VALUE, SECRET_SOURCE, QUOTE, "leaky message"):
        assert secret not in out


def test_script_survives_missing_schema_details_and_diagnostics(db_session, doc, capsys):
    code, out = _run_script(db_session, doc, FakeExtractor({f"{SECRET_SOURCE} {QUOTE}": ExtractionValidationError("x")}), capsys)
    assert code == script.EXIT_REQUEST_FAILED
    assert "error_category: SCHEMA_VALIDATION_FAILED" in out
    assert "validation_error_count: unknown" in out and "validation_errors: unknown" in out
    assert "request_id: unknown" in out and "finish_reason: unknown" in out


@pytest.mark.parametrize(
    ("exc", "category"),
    [
        (ExtractionTruncatedError("x", ResponseDiagnostics(finish_reason="length")), "RESPONSE_TRUNCATED"),
        (ExtractionRefusalError("x", ResponseDiagnostics(finish_reason="stop")), "MODEL_REFUSAL"),
        (ExtractionUnexpectedToolCallsError("x", ResponseDiagnostics(finish_reason="tool_calls")), "UNEXPECTED_TOOL_CALLS"),
        (ExtractionUnexpectedFinishReasonError("x", ResponseDiagnostics(finish_reason="weird")), "UNEXPECTED_FINISH_REASON"),
    ],
)
def test_other_failure_categories_unchanged_and_have_no_validation_lines(db_session, doc, capsys, exc, category):
    _, out = _run_script(db_session, doc, FakeExtractor({f"{SECRET_SOURCE} {QUOTE}": exc}), capsys)
    assert f"error_category: {category}" in out and "validation_error" not in out


def test_script_gates_still_block_everything(capsys):
    def boom(*a, **k):
        raise AssertionError("must not run")

    argv = ["--document-id", "00000000-0000-0000-0000-000000000001", "--page-number", "1",
            "--chunk-id", "00000000-0000-0000-0000-000000000002"]
    for execute, env in [(False, {"RUN_META_DIAGNOSTIC": "true"}), (True, {})]:
        code = script.main((["--execute"] if execute else []) + argv, env=env, session_factory=boom, extractor_factory=boom)
        assert code == script.EXIT_REFUSED
