"""Privacy and correlation guarantees of the structured JSON logging layer."""

from __future__ import annotations

import json
import logging

import pytest

from app.core import logging_config as lc

SOURCE_QUOTE = "The department must submit the report by 30 November."
SECRET = "sk-live-abcdef123456"
LONG_VALUE = "x" * 600


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@pytest.fixture
def capture():
    logger = logging.getLogger("pramaan.test.privacy")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    handler = _Capture()
    handler.setFormatter(lc.build_formatter())
    logger.addHandler(handler)
    try:
        yield logger, handler
    finally:
        logger.handlers.clear()


def _last(handler: _Capture) -> dict:
    assert handler.lines, "no log line was captured"
    return json.loads(handler.lines[-1])


def test_credentials_and_source_content_are_redacted(capture):
    logger, handler = capture
    logger.info(
        "extraction_chunk_failed",
        extra={
            "event": "extraction.chunk_failed",
            "api_key": SECRET,
            "authorization": f"Bearer {SECRET}",
            "source_quote": SOURCE_QUOTE,
            "extracted_text": SOURCE_QUOTE,
            "model_response": SOURCE_QUOTE,
            "page_number": 2,
        },
    )
    payload = _last(handler)
    for key in ("api_key", "authorization", "source_quote", "extracted_text", "model_response"):
        assert payload[key] == lc.REDACTED
    assert SECRET not in handler.lines[-1]
    assert SOURCE_QUOTE not in handler.lines[-1]
    # Non-sensitive metadata survives untouched.
    assert payload["page_number"] == 2
    assert payload["event"] == "extraction.chunk_failed"


def test_safe_operational_fields_are_kept(capture):
    logger, handler = capture
    logger.info(
        "extraction_completed",
        extra={
            "event": "extraction.completed",
            "extraction_run_id": "run-1",
            "prompt_version": "v1.1-structured-extraction",
            "model_id": "muse-spark-1.3",
            "input_tokens": 1171,
            "output_tokens": 6623,
            "reasoning_tokens": 4473,
            "content_length": 3213,
            "finish_reason": "stop",
            "duration_ms": 20400.5,
        },
    )
    payload = _last(handler)
    assert payload["input_tokens"] == 1171
    assert payload["output_tokens"] == 6623
    assert payload["finish_reason"] == "stop"
    assert payload["prompt_version"] == "v1.1-structured-extraction"
    assert payload["content_length"] == 3213


def test_long_values_are_truncated(capture):
    logger, handler = capture
    logger.info("long", extra={"event": "test.long", "detail": LONG_VALUE})
    payload = _last(handler)
    assert payload["detail"].endswith("…")
    assert len(payload["detail"]) == lc.MAX_FIELD_VALUE_LENGTH + 1


def test_request_id_is_bound_from_context(capture):
    logger, handler = capture
    token = lc.bind_request_id("req-abc")
    try:
        logger.info("with_context", extra={"event": "http.request_completed"})
        logger.info(
            "explicit_wins", extra={"event": "http.request_completed", "request_id": "req-explicit"}
        )
    finally:
        lc.reset_request_id(token)

    first, second = handler.lines[-2], handler.lines[-1]
    assert json.loads(first)["request_id"] == "req-abc"
    assert json.loads(second)["request_id"] == "req-explicit"


def test_output_is_single_line_json_with_service_fields(capture):
    logger, handler = capture
    logger.info("shape", extra={"event": "test.shape"})
    line = handler.lines[-1]
    assert "\n" not in line
    payload = json.loads(line)
    assert payload["severity"] == "INFO"
    assert payload["service"] == lc.SERVICE_NAME
    assert payload["environment"]
    assert "timestamp" in payload


def test_redaction_never_mutates_the_record():
    record = logging.LogRecord("pramaan.test", logging.INFO, __file__, 1, "evt", None, None)
    record.api_key = SECRET
    record.source_quote = SOURCE_QUOTE
    payload = json.loads(lc.build_formatter().format(record))
    assert payload["api_key"] == lc.REDACTED
    assert payload["source_quote"] == lc.REDACTED
    assert record.api_key == SECRET
    assert record.source_quote == SOURCE_QUOTE


@pytest.mark.parametrize(
    "name",
    [
        "api_key",
        "model_api_key",
        "authorization",
        "source_quote",
        "extracted_text",
        "quotation",
        "raw_response",
        "access_token",
        "database_url",
        "prompt",
        "content",
        "text",
    ],
)
def test_sensitive_field_names_are_recognised(name):
    assert lc.is_sensitive_field(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "event",
        "request_id",
        "extraction_run_id",
        "page_number",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "prompt_version",
        "content_length",
        "finish_reason",
        "route",
        "status_code",
    ],
)
def test_operational_field_names_are_not_redacted(name):
    assert lc.is_sensitive_field(name) is False


def test_configure_logging_installs_json_handler():
    lc.configure_logging()
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, lc.SafeJsonFormatter)
