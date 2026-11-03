"""Response diagnostics and failure classification for the structured extractor (Milestone 4B.5C.2).

All offline: the Meta client is replaced by a stub returning precisely-shaped SDK-like objects.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.ingestion.structured_extractor import (
    EmptyResponseError,
    ExtractionInvalidContentError,
    ExtractionJSONParseError,
    ExtractionRefusalError,
    ExtractionTruncatedError,
    ExtractionUnexpectedFinishReasonError,
    ExtractionUnexpectedToolCallsError,
    ExtractionValidationError,
    ResponseDiagnostics,
    StructuredExtractionError,
    StructuredExtractor,
)

VALID = json.dumps(
    {
        "facts": [{"fact_type": "DATE", "raw_value": "1 Nov", "source_quote": "On 1 Nov the rule applies."}],
        "requirements": [],
    }
)
SOURCE = "SECRET-SOURCE-TEXT-123 must never be logged."
LEAK = "SECRET-MODEL-OUTPUT-456"
API_KEY = "sk-test-SECRET-KEY-789"


def make_response(
    *,
    content="__default__",
    finish="stop",
    refusal=None,
    tool_calls=None,
    usage=(922, 1911, 1500),
    rid="chatcmpl-abc123",
    model="muse-spark-1.3",
    with_choice=True,
):
    message = SimpleNamespace(
        content=VALID if content == "__default__" else content,
        refusal=refusal,
        tool_calls=tool_calls,
        function_call=None,
    )
    choice = SimpleNamespace(message=message, finish_reason=finish)
    if usage is None:
        usage_obj = None
    else:
        details = None if usage[2] is None else SimpleNamespace(reasoning_tokens=usage[2])
        usage_obj = SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1], completion_tokens_details=details)
    return SimpleNamespace(id=rid, model=model, choices=[choice] if with_choice else [], usage=usage_obj)


@pytest.fixture
def run(monkeypatch):
    def _run(response, source=SOURCE) -> object:
        client = MagicMock()
        client.chat.completions.create.return_value = response
        monkeypatch.setattr("app.ingestion.structured_extractor.get_meta_client", lambda: client)
        return StructuredExtractor(Settings(model_api_key=API_KEY)).extract_from_text(source)

    return _run


# 1. success metadata
def test_success_captures_safe_metadata(run):
    result = run(make_response())
    d = result.diagnostics
    assert isinstance(d, ResponseDiagnostics)
    assert (d.request_id, d.response_model, d.finish_reason) == ("chatcmpl-abc123", "muse-spark-1.3", "stop")
    assert (d.input_tokens, d.output_tokens, d.reasoning_tokens) == (922, 1911, 1500)
    assert (d.content_type, d.content_length) == ("str", len(VALID))
    assert (d.refusal_present, d.tool_calls_count, d.choices_count) == (False, 0, 1)
    assert d.latency_ms is not None and d.latency_ms >= 0
    assert (result.request_id, result.input_tokens, result.output_tokens) == ("chatcmpl-abc123", 922, 1911)


# 2-5. empty variants keep diagnostics
def test_empty_choices_keeps_request_id_and_usage(run):
    with pytest.raises(EmptyResponseError) as info:
        run(make_response(with_choice=False))
    d = info.value.diagnostics
    assert d.choices_count == 0 and d.request_id == "chatcmpl-abc123"
    assert (d.input_tokens, d.output_tokens) == (922, 1911)  # 11/12: survive an empty response


def test_null_content_classified_empty_with_diagnostics(run):
    with pytest.raises(EmptyResponseError) as info:
        run(make_response(content=None, finish="stop"))
    d = info.value.diagnostics
    assert (d.content_type, d.content_length) == ("NoneType", None) and d.finish_reason == "stop"


@pytest.mark.parametrize(("content", "length"), [("", 0), ("   ", 3), ("\n\t ", 3)])
def test_empty_and_whitespace_content(run, content, length):
    with pytest.raises(EmptyResponseError, match="empty or whitespace-only") as info:
        run(make_response(content=content))
    assert info.value.diagnostics.content_length == length


# 6-7. truncation
def test_finish_reason_length_is_truncated_even_with_valid_json(run):
    with pytest.raises(ExtractionTruncatedError) as info:
        run(make_response(content=VALID, finish="length", usage=(900, 3000, 2950)))
    d = info.value.diagnostics
    assert d.finish_reason == "length" and d.content_length == len(VALID)
    assert (d.input_tokens, d.output_tokens, d.reasoning_tokens) == (900, 3000, 2950)  # 13


def test_truncated_content_is_never_parsed(run, monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("truncated output must not be parsed")

    monkeypatch.setattr("app.ingestion.structured_extractor.json.loads", forbidden)
    with pytest.raises(ExtractionTruncatedError):
        run(make_response(content=VALID, finish="length"))


def test_truncation_with_no_content_is_truncated_not_empty(run):
    with pytest.raises(ExtractionTruncatedError):
        run(make_response(content=None, finish="length", usage=(900, 3000, 3000)))


# 8. refusal
def test_refusal_classified_separately_without_leaking_text(run):
    with pytest.raises(ExtractionRefusalError) as info:
        run(make_response(content=None, refusal=f"I cannot help {LEAK}"))
    assert info.value.diagnostics.refusal_present is True
    assert LEAK not in str(info.value)


def test_content_filter_is_refusal_class(run):
    with pytest.raises(ExtractionRefusalError) as info:
        run(make_response(content=None, finish="content_filter"))
    assert info.value.category == "MODEL_REFUSAL"


def test_refusal_metadata_alongside_valid_content_still_parses(run):
    assert run(make_response(refusal="note")).response.facts  # content is usable: not a refusal


# 9. tool calls
@pytest.mark.parametrize("finish", ["tool_calls", "stop"])
def test_unexpected_tool_calls_classified(run, finish):
    calls = [SimpleNamespace(id="t1", function=SimpleNamespace(name="f", arguments=LEAK))]
    with pytest.raises(ExtractionUnexpectedToolCallsError) as info:
        run(make_response(content=None, finish=finish, tool_calls=calls))
    assert info.value.diagnostics.tool_calls_count == 1
    assert LEAK not in str(info.value)  # tool arguments never surface


# 10. unknown / non-success finish reasons
@pytest.mark.parametrize("finish", ["weird", None, "end_turn"])
def test_unknown_finish_reason_never_succeeds(run, finish):
    with pytest.raises(ExtractionUnexpectedFinishReasonError) as info:
        run(make_response(content=VALID, finish=finish))
    assert info.value.diagnostics.finish_reason == finish


def test_non_string_content_is_invalid_content(run):
    with pytest.raises(ExtractionInvalidContentError) as info:
        run(make_response(content=[{"type": "text", "text": LEAK}]))
    assert isinstance(info.value, ExtractionJSONParseError)  # maps to the INVALID_JSON page reason
    assert LEAK not in str(info.value) and info.value.diagnostics.content_type == "list"


# 14. missing usage stays unknown
def test_missing_usage_is_none_not_zero(run):
    result = run(make_response(usage=None))
    d = result.diagnostics
    assert d.input_tokens is None and d.output_tokens is None and d.reasoning_tokens is None
    assert result.input_tokens is None and result.output_tokens is None


def test_missing_reasoning_detail_is_none(run):
    d = run(make_response(usage=(10, 20, None))).diagnostics
    assert (d.input_tokens, d.output_tokens, d.reasoning_tokens) == (10, 20, None)


def test_parse_and_validation_failures_keep_usage(run):
    with pytest.raises(ExtractionJSONParseError) as info:
        run(make_response(content="not json"))
    assert info.value.diagnostics.output_tokens == 1911
    with pytest.raises(ExtractionValidationError) as info2:
        run(make_response(content=json.dumps({"facts": "oops"})))
    assert info2.value.diagnostics.request_id == "chatcmpl-abc123"


def test_exception_categories_are_stable():
    assert ExtractionTruncatedError.category == "RESPONSE_TRUNCATED"
    assert ExtractionRefusalError.category == "MODEL_REFUSAL"
    assert ExtractionUnexpectedToolCallsError.category == "UNEXPECTED_TOOL_CALLS"
    assert EmptyResponseError.category == "EMPTY_RESPONSE"
    assert issubclass(ExtractionTruncatedError, StructuredExtractionError)


# 15-17, 19. logging safety
def _all_log_text(caplog) -> str:
    parts = []
    for record in caplog.records:
        parts.append(record.getMessage())
        parts.append(repr({k: v for k, v in record.__dict__.items() if k not in ("args", "msg")}))
    return "\n".join(parts)


@pytest.mark.parametrize(
    "response",
    [
        make_response(content=f'{{"facts":[{{"fact_type":"BOGUS","raw_value":"{LEAK}","source_quote":"q"}}],"requirements":[]}}'),
        make_response(content=LEAK, finish="length"),
        make_response(content=None, refusal=f"refusal {LEAK}"),
        make_response(content=LEAK),
        make_response(content=None, finish="tool_calls", tool_calls=[SimpleNamespace(function=SimpleNamespace(arguments=LEAK))]),
    ],
)
def test_logs_and_errors_never_contain_content_prompt_or_key(run, caplog, response):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(StructuredExtractionError) as info:
        run(response)
    text = _all_log_text(caplog) + str(info.value)
    for secret in (LEAK, "SECRET-SOURCE-TEXT-123", API_KEY, "faithful structured operational information"):
        assert secret not in text


def test_success_logs_contain_no_content_prompt_or_key(run, caplog):
    caplog.set_level(logging.DEBUG)
    run(make_response())
    text = _all_log_text(caplog)
    for secret in ("SECRET-SOURCE-TEXT-123", API_KEY, "1 Nov the rule applies"):
        assert secret not in text


def test_rejection_log_has_safe_fields_and_unknown_is_none_not_zero(run, caplog):
    caplog.set_level(logging.WARNING)
    with pytest.raises(EmptyResponseError):
        run(make_response(content=None, usage=None))
    rec = next(r for r in caplog.records if getattr(r, "event", "") == "extraction.response_rejected")
    assert rec.error_category == "EMPTY_RESPONSE" and rec.request_id == "chatcmpl-abc123"
    assert rec.finish_reason == "stop" and rec.refusal_present is False and rec.tool_calls_count == 0
    assert rec.input_tokens is None and rec.output_tokens is None and rec.reasoning_tokens is None
    assert rec.model_id and rec.prompt_version and rec.latency_ms >= 0
