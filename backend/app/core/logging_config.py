"""Structured JSON logging for Cloud Logging, with privacy guards.

One JSON object per line on stdout. Every record carries ``severity``, ``timestamp``,
``service``, ``environment``, the stable ``event`` name supplied by the call site and,
inside an HTTP request, ``request_id``. Cloud Logging parses these fields directly, and
log-based metrics/alerts match on ``jsonPayload.event`` or ``jsonPayload.metric_name``.

Two privacy rules are enforced in the formatter, whatever the call site does:

1. A field whose *name* matches the sensitive policy is replaced with ``[REDACTED]``.
2. String values are truncated to ``MAX_FIELD_VALUE_LENGTH`` characters.

Redaction happens while building the serialized copy, so ``LogRecord`` objects are never
mutated and other consumers (e.g. pytest's ``caplog``) keep seeing the original record.
"""

from __future__ import annotations

import contextvars
import logging
import os

from pythonjsonlogger import jsonlogger

REQUEST_ID_HEADER = "X-Request-ID"
SERVICE_NAME = "pramaan-ai-backend"
REDACTED = "[REDACTED]"
MAX_FIELD_VALUE_LENGTH = 512

_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "pramaan_request_id", default=None
)


def current_request_id() -> str | None:
    """Request id bound to the current context, if any."""
    return _REQUEST_ID.get()


def bind_request_id(request_id: str) -> contextvars.Token:
    """Bind a request id for the duration of one request (reset with the returned token)."""
    return _REQUEST_ID.set(request_id)


def reset_request_id(token: contextvars.Token) -> None:
    _REQUEST_ID.reset(token)


# Sending any of these to a log backend would leak a credential or source content.
_SENSITIVE_EXACT = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "password",
        "passphrase",
        "secret",
        "client_secret",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "token",
        "bearer",
        "credentials",
        "database_url",
        "db_url",
        "dsn",
        "connection_string",
        "content",
        "text",
        "prompt",
        "system_prompt",
        "user_prompt",
        "response",
        "raw_response",
        "model_response",
        "rejected_value",
        "input",
        "output",
        "body",
        "payload",
        "source_text",
        "source_quote",
        "quote",
        "quotation",
        "transcription",
        "file_data",
        "pdf_bytes",
    }
)

# Field-name suffixes that are content or credentials regardless of the prefix:
# ``source_quote``, ``extracted_text``, ``raw_response``, ``api_key``, ``access_token``...
_SENSITIVE_SUFFIXES = (
    "_quote",
    "_quotes",
    "_text",
    "_prompt",
    "_response",
    "_secret",
    "_password",
    "_token",
    "_api_key",
    "_key",
    "_transcription",
)


def is_sensitive_field(name: str) -> bool:
    """True when a log field name indicates credentials or source content."""
    lowered = name.lower().strip()
    if not lowered:
        return False
    if lowered in _SENSITIVE_EXACT:
        return True
    return lowered.endswith(_SENSITIVE_SUFFIXES)


class SafeJsonFormatter(jsonlogger.JsonFormatter):
    """JSON formatter that adds service/request context and applies the privacy policy."""

    def add_fields(
        self,
        log_record: dict,
        record: logging.LogRecord,
        message_dict: dict,
    ) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record["service"] = SERVICE_NAME
        log_record["environment"] = os.getenv("APP_ENV", "local")
        if not log_record.get("request_id"):
            request_id = current_request_id()
            if request_id:
                log_record["request_id"] = request_id
        for key, value in list(log_record.items()):
            if is_sensitive_field(key):
                log_record[key] = REDACTED
            elif isinstance(value, str) and len(value) > MAX_FIELD_VALUE_LENGTH:
                log_record[key] = value[:MAX_FIELD_VALUE_LENGTH] + "…"


def build_formatter() -> jsonlogger.JsonFormatter:
    """Formatter used for application logs (also used directly by tests)."""
    return SafeJsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"levelname": "severity", "asctime": "timestamp"},
    )


def configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler()
    handler.setFormatter(build_formatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
