"""Metadata-only Langfuse tracing for document extraction.

Trace architecture (one extraction run):

    pramaan.extraction.run            one span  -> one Langfuse trace
      └─ pramaan.extraction.page      one span per page
           └─ muse-spark.extraction   one generation per actual Meta request

Privacy
    This module physically cannot send document text, quotations, prompts or model
    responses: no observation API here accepts content. Attributes pass through a fixed
    allowlist of identifiers/numbers/outcomes, string values are length-capped, and
    ``input``/``output`` are never set on any observation.

Failure isolation
    Tracing is optional. Missing or partial credentials, a disabled switch, an SDK import
    error, a construction error, or any exception while recording disables tracing for the
    process and logs a safe warning. Extraction, evidence verification, persistence,
    ModelUsage and AuditEvent writes are never affected, and nothing is retried (so no
    duplicate ModelUsage rows can be produced by tracing).

Correlation
    ``extraction_run_id`` appears on the root span and matches AuditEvent.request_id and
    ModelUsage rows; per-request ``request_id`` (Meta's id) appears on the generation.
"""

from __future__ import annotations

import contextvars
import logging
import threading
from collections.abc import Mapping
from typing import Any, Protocol

from app.core import metrics
from app.core.config import Settings, get_settings
from app.core.logging_config import is_sensitive_field

logger = logging.getLogger(__name__)

TRACE_NAME = "pramaan.extraction.run"
PAGE_SPAN_NAME = "pramaan.extraction.page"
GENERATION_NAME = "muse-spark.extraction"
LANGFUSE_TIMEOUT_SECONDS = 5
MAX_ATTRIBUTE_VALUE_LENGTH = 200

# Identifiers, counts and typed outcomes only. Content fields are absent by construction.
ALLOWED_ATTRIBUTE_KEYS = frozenset(
    {
        "extraction_run_id",
        "document_id",
        "model_id",
        "prompt_version",
        "page_count",
        "page_number",
        "chunk_count",
        "model_calls",
        "requests_succeeded",
        "requests_failed",
        "status",
        "outcome",
        "page_status",
        "reason",
        "error_category",
        "error_type",
        "duration_ms",
        "latency_ms",
        "request_id",
        "finish_reason",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "facts_count",
        "requirements_count",
        "facts_created",
        "requirements_created",
        "evidence_links_created",
        "proposals_skipped",
        "model_requests_succeeded",
        "model_requests_failed",
        "pages_processed",
        "pages_skipped",
        "pages_failed",
    }
)

_current_page: contextvars.ContextVar[PageTrace | None] = contextvars.ContextVar(
    "pramaan_trace_page", default=None
)


def sanitize_attributes(fields: Mapping[str, Any] | None) -> dict[str, Any]:
    """Allowlisted, length-capped, content-free attributes for Langfuse."""
    if not fields:
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in fields.items():
        if not isinstance(key, str) or key not in ALLOWED_ATTRIBUTE_KEYS:
            continue
        if is_sensitive_field(key):
            continue
        if value is None:
            continue
        if isinstance(value, bool):
            cleaned[key] = value
        elif isinstance(value, (int, float)):
            cleaned[key] = value
        elif isinstance(value, str):
            text = value.strip()
            if text:
                cleaned[key] = text[:MAX_ATTRIBUTE_VALUE_LENGTH]
    return cleaned


class _ObservationLike(Protocol):
    def update(self, **kwargs: Any) -> Any: ...

    def end(self, **kwargs: Any) -> Any: ...


class _ClientLike(Protocol):
    def start_observation(self, **kwargs: Any) -> Any: ...

    def flush(self) -> None: ...

    def shutdown(self) -> None: ...


class Tracer:
    """Thin, fault-tolerant wrapper over an injected Langfuse client (or None)."""

    def __init__(self, client: _ClientLike | None = None, *, environment: str = "local") -> None:
        self._client = client
        self.environment = environment
        self._failures = 0

    @property
    def active(self) -> bool:
        return self._client is not None

    @property
    def failures(self) -> int:
        return self._failures

    def _disable(self, stage: str, exc: BaseException) -> None:
        """Stop tracing for this process after a failure; never retry, never raise."""
        self._failures += 1
        self._client = None
        try:
            logger.warning(
                "tracing_disabled_after_failure",
                extra={"event": "tracing.failed", "stage": stage, "error_type": type(exc).__name__},
            )
            metrics.record_metric(metrics.METRIC_LANGFUSE_FAILURES, labels={"stage": stage})
        except Exception:
            return

    def start_run(
        self,
        *,
        metadata: Mapping[str, Any] | None = None,
        version: str | None = None,
        name: str = TRACE_NAME,
    ) -> RunTrace:
        client = self._client
        if client is None:
            return RunTrace(None, self)
        try:
            observation = client.start_observation(
                name=name,
                as_type="span",
                metadata=sanitize_attributes(metadata),
                version=version,
            )
        except Exception as exc:
            self._disable("run_start", exc)
            return RunTrace(None, self)
        if observation is None:
            return RunTrace(None, self)
        return RunTrace(observation, self)

    def flush(self) -> None:
        client = self._client
        if client is None:
            return
        try:
            client.flush()
        except Exception as exc:
            self._disable("flush", exc)

    def shutdown(self) -> None:
        client = self._client
        if client is None:
            return
        self._client = None
        try:
            client.shutdown()
        except Exception as exc:
            self._failures += 1
            logger.warning(
                "tracing_shutdown_failed",
                extra={"event": "tracing.failed", "stage": "shutdown", "error_type": type(exc).__name__},
            )


class GenerationTrace:
    """One Muse Spark request. Accepts numbers and identifiers, never content."""

    __slots__ = ("_observation", "_tracer", "_ended")

    def __init__(self, observation: _ObservationLike | None, tracer: Tracer) -> None:
        self._observation = observation
        self._tracer = tracer
        self._ended = observation is None

    @property
    def active(self) -> bool:
        return not self._ended

    def succeed(
        self,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        latency_ms: float | None = None,
        request_id: str | None = None,
        finish_reason: str | None = None,
    ) -> None:
        usage: dict[str, int] = {}
        for key, value in (("input", input_tokens), ("output", output_tokens)):
            if isinstance(value, int) and not isinstance(value, bool):
                usage[key] = value
        self._close(
            level="DEFAULT",
            status_message="success",
            metadata={
                "outcome": "success",
                "request_id": request_id,
                "finish_reason": finish_reason,
                "latency_ms": latency_ms,
                "reasoning_tokens": reasoning_tokens,
            },
            usage_details=usage or None,
        )

    def fail(self, error_category: str) -> None:
        self._close(
            level="ERROR",
            status_message=error_category,
            metadata={"outcome": "failure", "error_category": error_category},
        )

    def end(self) -> None:
        """Close a generation that was never classified, so nothing stays open."""
        self._close(level="WARNING", status_message="NO_OUTCOME", metadata={"outcome": "unknown"})

    def _close(
        self,
        *,
        level: str,
        status_message: str,
        metadata: Mapping[str, Any] | None = None,
        usage_details: Mapping[str, int] | None = None,
    ) -> None:
        if self._ended:
            return
        self._ended = True
        observation = self._observation
        if observation is None:
            return
        kwargs: dict[str, Any] = {"level": level, "status_message": status_message}
        cleaned = sanitize_attributes(metadata)
        if cleaned:
            kwargs["metadata"] = cleaned
        if usage_details:
            kwargs["usage_details"] = dict(usage_details)
        try:
            observation.update(**kwargs)
            observation.end()
        except Exception as exc:
            self._tracer._disable("generation", exc)


class PageTrace:
    """One page of an extraction run."""

    __slots__ = ("_observation", "_tracer", "_ended")

    def __init__(self, observation: _ObservationLike | None, tracer: Tracer) -> None:
        self._observation = observation
        self._tracer = tracer
        self._ended = observation is None

    @property
    def active(self) -> bool:
        return not self._ended

    def activate(self) -> contextvars.Token:
        """Make this the page generations attach to for the duration of a model call."""
        return _current_page.set(self)

    def deactivate(self, token: contextvars.Token) -> None:
        try:
            _current_page.reset(token)
        except ValueError:  # different context; nothing to restore
            return

    def generation(self, *, model_id: str, prompt_version: str) -> GenerationTrace:
        if self._ended or self._observation is None:
            return GenerationTrace(None, self._tracer)
        try:
            child = self._observation.start_observation(
                name=GENERATION_NAME,
                as_type="generation",
                model=model_id,
                version=prompt_version,
                metadata=sanitize_attributes(
                    {"model_id": model_id, "prompt_version": prompt_version}
                ),
            )
        except Exception as exc:
            self._tracer._disable("generation_start", exc)
            return GenerationTrace(None, self._tracer)
        if child is None:
            return GenerationTrace(None, self._tracer)
        return GenerationTrace(child, self._tracer)

    def update(self, **fields: Any) -> None:
        if self._ended or self._observation is None:
            return
        cleaned = sanitize_attributes(fields)
        if not cleaned:
            return
        try:
            self._observation.update(metadata=cleaned)
        except Exception as exc:
            self._tracer._disable("page_update", exc)

    def finish(self, **fields: Any) -> None:
        if self._ended:
            return
        self._ended = True
        observation = self._observation
        if observation is None:
            return
        cleaned = sanitize_attributes(fields)
        try:
            if cleaned:
                observation.update(metadata=cleaned)
            observation.end()
        except Exception as exc:
            self._tracer._disable("page_end", exc)


class RunTrace:
    """The trace for one extraction run (root span)."""

    __slots__ = ("_observation", "_tracer", "_ended")

    def __init__(self, observation: _ObservationLike | None, tracer: Tracer) -> None:
        self._observation = observation
        self._tracer = tracer
        self._ended = observation is None
        self._tracer = tracer

    @property
    def active(self) -> bool:
        return not self._ended

    def page(self, page_number: int, **fields: Any) -> PageTrace:
        if self._ended or self._observation is None:
            return PageTrace(None, self._tracer)
        payload = {"page_number": page_number, **fields}
        try:
            child = self._observation.start_observation(
                name=PAGE_SPAN_NAME, as_type="span", metadata=sanitize_attributes(payload)
            )
        except Exception as exc:
            self._tracer._disable("page_start", exc)
            return PageTrace(None, self._tracer)
        if child is None:
            return PageTrace(None, self._tracer)
        return PageTrace(child, self._tracer)

    def finish(self, *, flush: bool = True, **fields: Any) -> None:
        if self._ended:
            return
        self._ended = True
        observation = self._observation
        if observation is None:
            return
        cleaned = sanitize_attributes(fields)
        try:
            if cleaned:
                observation.update(metadata=cleaned)
            observation.end()
        except Exception as exc:
            self._tracer._disable("run_end", exc)
        if flush:
            self._tracer.flush()


# ── Process-level tracer ──────────────────────────────────────────────────────

_tracer: Tracer | None = None
_tracer_lock = threading.Lock()


def _build_tracer() -> Tracer:
    """Create the process tracer from settings. Never raises."""
    try:
        settings: Settings = get_settings()
    except Exception as exc:  # invalid configuration must not break extraction
        logger.warning(
            "tracing_configuration_invalid",
            extra={"event": "tracing.inactive", "reason": "invalid_settings", "error_type": type(exc).__name__},
        )
        return Tracer(None)

    environment = settings.app_env
    if not settings.langfuse_enabled:
        logger.info(
            "tracing_inactive",
            extra={"event": "tracing.inactive", "reason": "disabled_by_config"},
        )
        return Tracer(None, environment=environment)

    public_key = (settings.langfuse_public_key or "").strip()
    secret_key = (settings.langfuse_secret_key or "").strip()
    if not public_key and not secret_key:
        logger.info(
            "tracing_inactive",
            extra={"event": "tracing.inactive", "reason": "missing_credentials"},
        )
        return Tracer(None, environment=environment)
    if not public_key or not secret_key:
        logger.warning(
            "tracing_inactive",
            extra={"event": "tracing.inactive", "reason": "partial_credentials"},
        )
        return Tracer(None, environment=environment)

    try:
        from langfuse import Langfuse
    except Exception as exc:
        logger.warning(
            "tracing_inactive",
            extra={"event": "tracing.inactive", "reason": "sdk_unavailable", "error_type": type(exc).__name__},
        )
        return Tracer(None, environment=environment)

    try:
        client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=settings.langfuse_base_url,
            timeout=LANGFUSE_TIMEOUT_SECONDS,
            tracing_enabled=True,
            environment=environment,
            release="pramaan-ai-backend",
        )
    except Exception as exc:
        logger.warning(
            "tracing_disabled_after_failure",
            extra={"event": "tracing.failed", "stage": "init", "error_type": type(exc).__name__},
        )
        metrics.record_metric(metrics.METRIC_LANGFUSE_FAILURES, labels={"stage": "init"})
        return Tracer(None, environment=environment)

    logger.info(
        "tracing_active",
        extra={
            "event": "tracing.active",
            "base_url": settings.langfuse_base_url,
            "environment": environment,
        },
    )
    return Tracer(client, environment=environment)


def get_tracer() -> Tracer:
    global _tracer
    if _tracer is None:
        with _tracer_lock:
            if _tracer is None:
                _tracer = _build_tracer()
    return _tracer


def reset_tracer() -> None:
    """Forget the cached tracer (tests; next use rebuilds it from settings)."""
    global _tracer
    with _tracer_lock:
        _tracer = None


def override_tracer(tracer: Tracer | None) -> None:
    """Install a specific tracer (tests and diagnostic scripts)."""
    global _tracer
    with _tracer_lock:
        _tracer = tracer


def start_extraction_trace(
    *,
    extraction_run_id: str,
    model_id: str,
    prompt_version: str,
    page_count: int | None = None,
    document_id: str | None = None,
) -> RunTrace:
    """Start the single trace for one extraction run."""
    return get_tracer().start_run(
        metadata={
            "extraction_run_id": extraction_run_id,
            "document_id": document_id,
            "model_id": model_id,
            "prompt_version": prompt_version,
            "page_count": page_count,
        },
        version=prompt_version,
    )


def open_generation(*, model_id: str, prompt_version: str) -> GenerationTrace:
    """Open the generation for a model request inside the active page span."""
    page = _current_page.get()
    if page is None:
        return GenerationTrace(None, get_tracer())
    return page.generation(model_id=model_id, prompt_version=prompt_version)


def flush_tracing() -> None:
    get_tracer().flush()


def shutdown_tracing() -> None:
    """Shut the tracer down only if tracing was actually initialized in this process."""
    tracer = _tracer
    if tracer is None:
        return
    tracer.shutdown()
