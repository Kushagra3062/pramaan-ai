"""
Document-level structured extraction orchestration (Milestone 4B.3C).

Workflow:
  canonical pages -> canonical chunks -> Muse Spark (one call per chunk) -> Pydantic
  validation (inside the extractor) -> per-page aggregation -> deterministic evidence
  verification + idempotent persistence (extraction_persistence_service) -> ModelUsage
  -> one document-level AuditEvent -> single commit.

Muse Spark proposes; the backend verifies; nothing here approves anything.

Two phases, so the document row lock is never held across external model requests:
  Phase 1 (read-only, no locks): call the model for every eligible page, in page order and
          chunk order, buffering responses and usage in memory.
  Phase 2 (write): persist each fully-extracted page via ``persist_page_extraction`` (which
          takes the document lock, held until our commit), write ModelUsage rows and the
          audit event, then commit exactly once.

Failure policy
  * Page not COMPLETED / empty canonical text / no canonical chunks -> page SKIPPED.
  * Any chunk request fails (API, empty response, bad JSON, schema violation) -> page
    FAILED; none of that page's candidates are persisted (no partial pages). A failed call
    is never treated as an empty successful extraction.
  * API-level failures (rate limit, timeout, connection, HTTP status, unexpected client
    error) or an unavailable extractor are systemic -> no further model calls; remaining
    eligible pages are NOT_PROCESSED. Pages extracted before the failure are still persisted.
    Parse/validation/empty-response failures are page-local; the run continues.
  * Document outcome: COMPLETED (every page PROCESSED), PARTIAL (>=1 PROCESSED, others not),
    FAILED (no page PROCESSED). Statuses are response-only; no database enum is added.
  * Any database/persistence exception -> the whole run is rolled back (facts, requirements,
    links, usage, audit) and ExtractionRunAbortedError is raised.

Model prompts contain only canonical chunk text, never backend identifiers.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import metrics
from app.core.tracing import PageTrace, RunTrace, start_extraction_trace
from app.db.repositories.audit_eval_repo import AuditRepository, ModelUsageRepository
from app.db.repositories.document_repo import DocumentPageRepository, DocumentRepository
from app.ingestion.chunking import clean_canonical_text
from app.ingestion.structured_extractor import (
    EmptyResponseError,
    ExtractionAPIError,
    ExtractionAPIStatusError,
    ExtractionConnectionError,
    ExtractionJSONParseError,
    ExtractionRateLimitError,
    ExtractionRefusalError,
    ExtractionTimeoutError,
    ExtractionTruncatedError,
    ExtractionUnexpectedFinishReasonError,
    ExtractionUnexpectedToolCallsError,
    ExtractionValidationError,
    StructuredExtractionResult,
    StructuredExtractor,
)
from app.models.document import DocumentChunk
from app.models.enums import DocumentProcessingStatus, ModelUsageStatus
from app.schemas.extraction import (
    ExtractedFactCandidate,
    RequirementCandidate,
    StructuredExtractionResponse,
)
from app.services.document_service import DocumentNotFoundError
from app.services.extraction_persistence_service import (
    PageNotCanonicalError,
    PageNotFoundError,
    PersistenceProvenance,
    PersistenceResult,
    persist_page_extraction,
)
from app.services.model_budget import (
    ModelBudget,
    ModelBudgetExceededError,
    Reservation,
    get_model_budget,
)

logger = logging.getLogger(__name__)

MODEL_PROVIDER = "meta"  # matches the existing model_usage convention
AUDIT_ENTITY_TYPE = "document"
AUDIT_EVENT_COMPLETED = "document.extraction_completed"
AUDIT_EVENT_PARTIAL = "document.extraction_partial"
AUDIT_EVENT_FAILED = "document.extraction_failed"
_REQUEST_ID_MAX = 100  # model_usage.request_id is String(100)


class ExtractionRunAbortedError(Exception):
    """A database/persistence failure aborted the run; everything was rolled back."""


class ExtractorLike(Protocol):
    model_id: str
    prompt_version: str

    def extract_from_text(self, source_text: str) -> StructuredExtractionResult: ...


# ── Response-only statuses and results ────────────────────────────────────────


class RunStatus(StrEnum):
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class PageStatus(StrEnum):
    PROCESSED = "PROCESSED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    NOT_PROCESSED = "NOT_PROCESSED"


class PageReason(StrEnum):
    PAGE_NOT_COMPLETED = "PAGE_NOT_COMPLETED"
    EMPTY_CANONICAL_TEXT = "EMPTY_CANONICAL_TEXT"
    NO_CANONICAL_CHUNKS = "NO_CANONICAL_CHUNKS"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    API_STATUS_ERROR = "API_STATUS_ERROR"
    API_ERROR = "API_ERROR"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    INVALID_JSON = "INVALID_JSON"
    RESPONSE_TRUNCATED = "RESPONSE_TRUNCATED"
    MODEL_REFUSAL = "MODEL_REFUSAL"
    UNEXPECTED_TOOL_CALLS = "UNEXPECTED_TOOL_CALLS"
    UNEXPECTED_FINISH_REASON = "UNEXPECTED_FINISH_REASON"
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    EXTRACTOR_UNAVAILABLE = "EXTRACTOR_UNAVAILABLE"
    RUN_STOPPED_AFTER_SYSTEMIC_ERROR = "RUN_STOPPED_AFTER_SYSTEMIC_ERROR"
    PAGE_STATE_CHANGED = "PAGE_STATE_CHANGED"


@dataclass
class PageRunResult:
    page_number: int
    status: PageStatus
    reason: PageReason | None = None
    page_extraction_status: str | None = None
    chunks_processed: int = 0
    requests_succeeded: int = 0
    requests_failed: int = 0
    facts_created: int = 0
    facts_reused: int = 0
    requirements_created: int = 0
    requirements_reused: int = 0
    evidence_links_created: int = 0
    proposals_skipped: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)


@dataclass
class ExtractionRunResult:
    workspace_id: uuid.UUID
    document_id: uuid.UUID
    extraction_run_id: str
    model_id: str
    prompt_version: str
    started_at: datetime
    completed_at: datetime
    duration_ms: float
    status: RunStatus
    page_count: int
    pages: list[PageRunResult]

    def _sum(self, attr: str) -> int:
        return sum(getattr(p, attr) for p in self.pages)

    def _pages_with(self, status: PageStatus) -> int:
        return sum(1 for p in self.pages if p.status is status)

    @property
    def pages_processed(self) -> int:
        return self._pages_with(PageStatus.PROCESSED)

    @property
    def pages_skipped(self) -> int:
        return self._pages_with(PageStatus.SKIPPED)

    @property
    def pages_failed(self) -> int:
        return self._pages_with(PageStatus.FAILED)

    @property
    def pages_not_processed(self) -> int:
        return self._pages_with(PageStatus.NOT_PROCESSED)

    @property
    def model_requests_succeeded(self) -> int:
        return self._sum("requests_succeeded")

    @property
    def model_requests_failed(self) -> int:
        return self._sum("requests_failed")

    @property
    def chunk_requests(self) -> int:
        return self.model_requests_succeeded + self.model_requests_failed

    @property
    def facts_created(self) -> int:
        return self._sum("facts_created")

    @property
    def facts_reused(self) -> int:
        return self._sum("facts_reused")

    @property
    def requirements_created(self) -> int:
        return self._sum("requirements_created")

    @property
    def requirements_reused(self) -> int:
        return self._sum("requirements_reused")

    @property
    def evidence_links_created(self) -> int:
        return self._sum("evidence_links_created")

    @property
    def proposals_skipped(self) -> int:
        return self._sum("proposals_skipped")


# ── Internals ─────────────────────────────────────────────────────────────────


@dataclass
class _UsageRecord:
    status: ModelUsageStatus
    model_id: str
    latency_ms: float
    request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None  # diagnostics only; model_usage has no such column


_PAGE_LOCAL_FAILURES: tuple[tuple[type[Exception], PageReason], ...] = (
    (EmptyResponseError, PageReason.EMPTY_RESPONSE),
    (ExtractionJSONParseError, PageReason.INVALID_JSON),
    (ExtractionValidationError, PageReason.SCHEMA_VALIDATION_FAILED),
    (ExtractionTruncatedError, PageReason.RESPONSE_TRUNCATED),
    (ExtractionRefusalError, PageReason.MODEL_REFUSAL),
    (ExtractionUnexpectedToolCallsError, PageReason.UNEXPECTED_TOOL_CALLS),
    (ExtractionUnexpectedFinishReasonError, PageReason.UNEXPECTED_FINISH_REASON),
)
_SYSTEMIC_FAILURES: tuple[tuple[type[Exception], PageReason], ...] = (
    (ExtractionRateLimitError, PageReason.RATE_LIMITED),
    (ExtractionTimeoutError, PageReason.TIMEOUT),
    (ExtractionConnectionError, PageReason.CONNECTION_ERROR),
    (ExtractionAPIStatusError, PageReason.API_STATUS_ERROR),
    (ExtractionAPIError, PageReason.API_ERROR),
)


def _classify_failure(exc: Exception) -> tuple[PageReason, bool, bool]:
    """Return (reason, is_systemic, request_was_made)."""
    for exc_type, reason in _SYSTEMIC_FAILURES:
        if isinstance(exc, exc_type):
            return reason, True, True
    for exc_type, reason in _PAGE_LOCAL_FAILURES:
        if isinstance(exc, exc_type):
            return reason, False, True
    return PageReason.EXTRACTOR_UNAVAILABLE, True, False


def _usage_status(reason: PageReason) -> ModelUsageStatus:
    return ModelUsageStatus.RATE_LIMITED if reason is PageReason.RATE_LIMITED else ModelUsageStatus.ERROR


def _schema_log_fields(exc: Exception) -> dict[str, object]:
    """Sanitized schema-rejection fields (count, loc, type only) for logging; empty otherwise."""
    schema_errors = getattr(exc, "schema_errors", None)
    if schema_errors is None:
        return {}
    return {
        "schema_error_count": schema_errors.error_count,
        "schema_errors": [e.as_dict() for e in schema_errors.errors],
        "schema_errors_truncated": schema_errors.truncated_error_list,
    }


def _apply_persistence(page_result: PageRunResult, persisted: PersistenceResult) -> None:
    page_result.facts_created = persisted.facts_created
    page_result.facts_reused = persisted.facts_reused
    page_result.requirements_created = persisted.requirements_created
    page_result.requirements_reused = persisted.requirements_reused
    page_result.evidence_links_created = persisted.evidence_links_created
    reasons = Counter(r.skip_reason.value for r in persisted.results if r.skip_reason is not None)
    page_result.skip_reasons = dict(reasons)
    page_result.proposals_skipped = sum(reasons.values())


# ── Entry point ───────────────────────────────────────────────────────────────


def run_document_extraction(
    session: Session,
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    extractor: ExtractorLike | None = None,
    model_budget: ModelBudget | None = None,
) -> ExtractionRunResult:
    """Run structured extraction over one document's canonical pages and chunks.

    Commits once at the end on success/partial/failed-with-audit. On any database or
    persistence exception the whole run is rolled back and ExtractionRunAbortedError raised.
    Raises DocumentNotFoundError (nothing written) for a missing, cross-workspace or deleted document.
    """
    run_id = str(uuid.uuid4())
    started_at = datetime.now(UTC)
    t0 = time.perf_counter()
    extractor = extractor or StructuredExtractor()
    budget = model_budget or get_model_budget()
    # Only extractors that make real provider requests charge the daily budget: a test fake
    # that never leaves the process must not consume production capacity.
    charges_budget = bool(getattr(extractor, "calls_model_provider", False))
    reservation: Reservation | None = None

    doc_repo = DocumentRepository(session)
    document = doc_repo.get_by_workspace_and_id(workspace_id, document_id)
    if (
        document is None
        or document.deleted_at is not None
        or document.processing_status
        in (DocumentProcessingStatus.DELETING.value, DocumentProcessingStatus.DELETED.value)
    ):
        raise DocumentNotFoundError("Document not found")

    trace: RunTrace | None = None
    try:
        pages = DocumentPageRepository(session).list_by_document(document_id)  # ordered by page_number
        chunk_rows = list(
            session.scalars(
                select(DocumentChunk)
                .where(
                    DocumentChunk.workspace_id == workspace_id,
                    DocumentChunk.document_id == document_id,
                )
                .order_by(DocumentChunk.page_start.asc(), DocumentChunk.chunk_index.asc())
            ).all()
        )

        trace = start_extraction_trace(
            extraction_run_id=run_id,
            model_id=extractor.model_id,
            prompt_version=extractor.prompt_version,
            page_count=len(pages),
            document_id=str(document_id),
        )

        # ── Phase 1: classify pages and call the model (no locks, no writes) ──
        page_results: dict[int, PageRunResult] = {}
        page_chunks: dict[int, list[str]] = {}
        page_traces: dict[int, PageTrace] = {}
        page_durations: dict[int, float] = {}
        eligible: list[int] = []
        for page in pages:
            n = page.page_number
            page_trace = trace.page(n)
            page_traces[n] = page_trace
            text = clean_canonical_text(page.extracted_text or "")
            if page.extraction_status != "COMPLETED":
                page_results[n] = PageRunResult(
                    n, PageStatus.SKIPPED, PageReason.PAGE_NOT_COMPLETED, page.extraction_status
                )
                page_trace.finish(
                    page_status=PageStatus.SKIPPED.value,
                    reason=PageReason.PAGE_NOT_COMPLETED.value,
                )
                continue
            if not text:
                page_results[n] = PageRunResult(
                    n, PageStatus.SKIPPED, PageReason.EMPTY_CANONICAL_TEXT, page.extraction_status
                )
                page_trace.finish(
                    page_status=PageStatus.SKIPPED.value,
                    reason=PageReason.EMPTY_CANONICAL_TEXT.value,
                )
                continue
            contents = [
                c.content
                for c in chunk_rows
                if c.page_start == n and c.page_end == n and c.content and c.content.strip()
            ]
            if not contents:
                page_results[n] = PageRunResult(
                    n, PageStatus.SKIPPED, PageReason.NO_CANONICAL_CHUNKS, page.extraction_status
                )
                page_trace.finish(
                    page_status=PageStatus.SKIPPED.value,
                    reason=PageReason.NO_CANONICAL_CHUNKS.value,
                )
                continue
            page_chunks[n] = contents
            page_results[n] = PageRunResult(n, PageStatus.NOT_PROCESSED, page_extraction_status=page.extraction_status)
            eligible.append(n)

        planned_calls = sum(len(page_chunks[n]) for n in eligible) if charges_budget else 0
        if planned_calls:
            # Atomic installation-wide reservation BEFORE the first model request. Insufficient
            # capacity rejects the whole run: nothing is written and nothing is sent, so a
            # document is never partially extracted because only some calls fit.
            reservation = budget.reserve(planned_calls)

        usage_records: list[_UsageRecord] = []
        page_candidates: dict[int, StructuredExtractionResponse] = {}
        stopped = False
        for n in eligible:
            pr = page_results[n]
            page_trace = page_traces[n]
            page_started = time.perf_counter()
            if stopped:
                pr.reason = PageReason.RUN_STOPPED_AFTER_SYSTEMIC_ERROR
                continue
            facts: list[ExtractedFactCandidate] = []
            reqs: list[RequirementCandidate] = []
            failed = False
            for content in page_chunks[n]:
                pr.chunks_processed += 1
                started = time.perf_counter()
                if reservation is not None:
                    reservation.consume()  # a consumed slot can never be released again
                token = page_trace.activate()  # a generation attaches to this page span
                try:
                    res = extractor.extract_from_text(content)
                except Exception as exc:  # classified below; never swallowed as an empty success
                    elapsed = round((time.perf_counter() - started) * 1000, 2)
                    reason, systemic, request_made = _classify_failure(exc)
                    diag = getattr(exc, "diagnostics", None)
                    pr.requests_failed += 1
                    if not request_made and reservation is not None:
                        reservation.refund()  # the request never left this process
                    if request_made:
                        # Real metadata from the response when one was received; otherwise unknown (None).
                        usage_records.append(
                            _UsageRecord(
                                _usage_status(reason),
                                extractor.model_id,
                                max(diag.latency_ms if diag and diag.latency_ms is not None else elapsed, 0.0),
                                diag.request_id if diag else None,
                                diag.input_tokens if diag else None,
                                diag.output_tokens if diag else None,
                                diag.reasoning_tokens if diag else None,
                            )
                        )
                    logger.warning(
                        "extraction_chunk_failed",
                        extra={
                            "event": "extraction.chunk_failed",
                            "extraction_run_id": run_id,
                            "document_id": str(document_id),
                            "page_number": n,
                            "reason": reason.value,
                            "error_type": type(exc).__name__,
                            "request_id": diag.request_id if diag else None,
                            "finish_reason": diag.finish_reason if diag else None,
                            "input_tokens": diag.input_tokens if diag else None,
                            "output_tokens": diag.output_tokens if diag else None,
                            "reasoning_tokens": diag.reasoning_tokens if diag else None,
                            "latency_ms": diag.latency_ms if diag and diag.latency_ms is not None else elapsed,
                            **_schema_log_fields(exc),
                        },
                    )
                    pr.status, pr.reason = PageStatus.FAILED, reason
                    failed = True
                    stopped = systemic
                    break
                finally:
                    page_trace.deactivate(token)
                pr.requests_succeeded += 1
                usage_records.append(
                    _UsageRecord(
                        ModelUsageStatus.SUCCESS,
                        res.model_id,
                        max(res.latency_ms, 0.0),
                        res.request_id,
                        res.input_tokens,
                        res.output_tokens,
                        res.diagnostics.reasoning_tokens if res.diagnostics else None,
                    )
                )
                facts.extend(res.response.facts)
                reqs.extend(res.response.requirements)
            page_durations[n] = round((time.perf_counter() - page_started) * 1000, 2)
            if not failed:
                page_candidates[n] = StructuredExtractionResponse(facts=facts, requirements=reqs)
                pr.status = PageStatus.PROCESSED  # may be downgraded in phase 2

        if reservation is not None:
            # Model phase finished (normal completion or a systemic stop): return capacity for
            # calls that were reserved but never attempted.
            reservation.release_unused()

        # ── Phase 2: persist (document lock acquired inside, held until commit) ──
        provenance = PersistenceProvenance(
            model_id=extractor.model_id,
            prompt_version=extractor.prompt_version,
            extraction_run_id=run_id,
            extracted_at=started_at,
        )
        for n in eligible:
            candidates = page_candidates.get(n)
            if candidates is None or not (candidates.facts or candidates.requirements):
                continue
            try:
                persisted = persist_page_extraction(
                    session,
                    workspace_id=workspace_id,
                    document_id=document_id,
                    page_number=n,
                    response=candidates,
                    provenance=provenance,
                )
            except (PageNotFoundError, PageNotCanonicalError):
                # Page changed since phase 1; the service raised before writing anything.
                page_results[n].status = PageStatus.FAILED
                page_results[n].reason = PageReason.PAGE_STATE_CHANGED
                continue
            _apply_persistence(page_results[n], persisted)

        ordered = [page_results[p.page_number] for p in pages]
        processed = sum(1 for r in ordered if r.status is PageStatus.PROCESSED)
        if processed == 0:
            status = RunStatus.FAILED
        elif processed == len(ordered):
            status = RunStatus.COMPLETED
        else:
            status = RunStatus.PARTIAL

        for pr in ordered:
            page_traces[pr.page_number].finish(
                page_status=pr.status.value,
                reason=pr.reason.value if pr.reason else None,
                model_calls=pr.chunks_processed,
                requests_succeeded=pr.requests_succeeded,
                requests_failed=pr.requests_failed,
                facts_created=pr.facts_created,
                requirements_created=pr.requirements_created,
                evidence_links_created=pr.evidence_links_created,
                proposals_skipped=pr.proposals_skipped,
                duration_ms=page_durations.get(pr.page_number, 0.0),
            )
            metrics.record_metric(
                metrics.METRIC_EXTRACTION_PAGES, labels={"status": pr.status.value}
            )

        usage_repo = ModelUsageRepository(session)
        for u in usage_records:
            usage_repo.record(
                provider=MODEL_PROVIDER,
                model_id=u.model_id,
                # model_usage token columns are NOT NULL (default 0): unknown usage is stored as 0.
                input_tokens=u.input_tokens or 0,
                output_tokens=u.output_tokens or 0,
                latency_ms=u.latency_ms,
                workspace_id=workspace_id,
                request_id=u.request_id[:_REQUEST_ID_MAX] if u.request_id else None,
                estimated_cost=None,
                status=u.status,
            )

        completed_at = datetime.now(UTC)
        duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        result = ExtractionRunResult(
            workspace_id=workspace_id,
            document_id=document_id,
            extraction_run_id=run_id,
            model_id=extractor.model_id,
            prompt_version=extractor.prompt_version,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            status=status,
            page_count=len(ordered),
            pages=ordered,
        )

        event_type = {
            RunStatus.COMPLETED: AUDIT_EVENT_COMPLETED,
            RunStatus.PARTIAL: AUDIT_EVENT_PARTIAL,
            RunStatus.FAILED: AUDIT_EVENT_FAILED,
        }[status]
        AuditRepository(session).log(
            event_type=event_type,
            entity_type=AUDIT_ENTITY_TYPE,
            workspace_id=workspace_id,
            entity_id=document_id,
            request_id=run_id,
            metadata={
                "extraction_run_id": run_id,
                "document_id": str(document_id),
                "workspace_id": str(workspace_id),
                "status": status.value,
                "model_id": extractor.model_id,
                "prompt_version": extractor.prompt_version,
                "page_count": result.page_count,
                "pages_processed": result.pages_processed,
                "pages_skipped": result.pages_skipped,
                "pages_failed": result.pages_failed,
                "pages_not_processed": result.pages_not_processed,
                "chunk_requests": result.chunk_requests,
                "model_requests_succeeded": result.model_requests_succeeded,
                "model_requests_failed": result.model_requests_failed,
                "facts_created": result.facts_created,
                "facts_reused": result.facts_reused,
                "requirements_created": result.requirements_created,
                "requirements_reused": result.requirements_reused,
                "evidence_links_created": result.evidence_links_created,
                "proposals_skipped": result.proposals_skipped,
                "duration_ms": duration_ms,
                # Aggregates over requests that actually reported usage; unknown usage is excluded,
                # never counted as zero (model_usage rows store unknown as 0 only because of NOT NULL).
                "input_tokens_known": sum(u.input_tokens for u in usage_records if u.input_tokens is not None),
                "output_tokens_known": sum(u.output_tokens for u in usage_records if u.output_tokens is not None),
                "reasoning_tokens_known": sum(
                    u.reasoning_tokens for u in usage_records if u.reasoning_tokens is not None
                ),
                "requests_with_unknown_usage": sum(
                    1 for u in usage_records if u.input_tokens is None or u.output_tokens is None
                ),
            },
        )

        session.commit()  # the single commit; releases the document lock
    except ModelBudgetExceededError:
        # Rejected before any write or request; the API maps this to HTTP 429.
        raise
    except Exception as exc:
        session.rollback()
        if reservation is not None:
            reservation.release_unused()
        logger.error(
            "extraction_run_aborted",
            extra={
                "event": "extraction.run_aborted",
                "extraction_run_id": run_id,
                "document_id": str(document_id),
                "error_type": type(exc).__name__,
            },
        )
        metrics.record_metric(metrics.METRIC_EXTRACTION_RUNS, labels={"status": "ABORTED"})
        if trace is not None:
            trace.finish(
                status="ABORTED",
                outcome="aborted",
                error_type=type(exc).__name__,
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
        raise ExtractionRunAbortedError(
            "Extraction run aborted and rolled back due to a database or persistence failure"
        ) from exc

    logger.info(
        "extraction_run_finished",
        extra={
            "event": "extraction.run_finished",
            "extraction_run_id": run_id,
            "document_id": str(document_id),
            "status": status.value,
            "duration_ms": duration_ms,
        },
    )
    metrics.record_metric(metrics.METRIC_EXTRACTION_RUNS, labels={"status": status.value})
    metrics.record_metric(
        metrics.METRIC_EXTRACTION_DURATION_MS,
        value=duration_ms,
        unit="ms",
        labels={"status": status.value},
    )
    if trace is not None:
        trace.finish(
            status=status.value,
            outcome=status.value.lower(),
            duration_ms=duration_ms,
            page_count=result.page_count,
            pages_processed=result.pages_processed,
            pages_skipped=result.pages_skipped,
            pages_failed=result.pages_failed,
            facts_created=result.facts_created,
            requirements_created=result.requirements_created,
            model_requests_succeeded=result.model_requests_succeeded,
            model_requests_failed=result.model_requests_failed,
        )
    return result
