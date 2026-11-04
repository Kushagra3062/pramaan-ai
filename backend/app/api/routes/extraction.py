"""
Structured extraction API: run extraction, list facts, list requirements.

Routes are workspace/document scoped under ``/api/v1/workspaces/{workspace_id}/documents``.

All routes require Supabase JWT authentication plus workspace membership
(``require_workspace_read`` for reads, ``require_workspace_write`` for the
extraction run). The extraction endpoint spends real model API usage; the
per-request call cap plus the installation-wide daily budget are cost controls,
not per-user rate limiting.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.auth import require_workspace_read, require_workspace_write
from app.api.dependencies import get_scoped_document
from app.core import metrics
from app.core.config import Settings, get_settings
from app.db.engine import get_db
from app.db.repositories.fact_repo import (
    EvidenceLinkRepository,
    FactRepository,
    RequirementRepository,
)
from app.ingestion.structured_extractor import StructuredExtractor
from app.models.document import Document
from app.models.enums import (
    DocumentProcessingStatus,
    FactType,
    RequirementStatus,
    VerificationStatus,
)
from app.schemas.extraction_api import (
    EvidenceReferenceResponse,
    ExtractedFactResponse,
    ExtractionRunResponse,
    PaginatedFactsResponse,
    PaginatedRequirementsResponse,
    RequirementResponse,
    build_provenance,
    split_normalized_value,
)
from app.services.document_service import DocumentNotFoundError
from app.services.extraction_orchestrator import (
    ExtractionRunAbortedError,
    ExtractorLike,
    PageReason,
    PageStatus,
    RunStatus,
    run_document_extraction,
)
from app.services.extraction_preflight import count_eligible_chunks
from app.services.model_budget import ModelBudgetExceededError, seconds_until_next_utc_day

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/documents", tags=["extraction"], dependencies=[Depends(require_workspace_read)])
logger = logging.getLogger(__name__)

# Failure-reason -> HTTP status. When a failed run has several reasons, the first match in
# this precedence order wins (most actionable/systemic first).
FAILURE_PRECEDENCE: tuple[tuple[PageReason, int], ...] = (
    (PageReason.EXTRACTOR_UNAVAILABLE, status.HTTP_503_SERVICE_UNAVAILABLE),
    (PageReason.RATE_LIMITED, status.HTTP_429_TOO_MANY_REQUESTS),
    (PageReason.TIMEOUT, status.HTTP_504_GATEWAY_TIMEOUT),
    (PageReason.CONNECTION_ERROR, status.HTTP_502_BAD_GATEWAY),
    (PageReason.API_STATUS_ERROR, status.HTTP_502_BAD_GATEWAY),
    (PageReason.API_ERROR, status.HTTP_502_BAD_GATEWAY),
    (PageReason.INVALID_JSON, status.HTTP_502_BAD_GATEWAY),
    (PageReason.EMPTY_RESPONSE, status.HTTP_502_BAD_GATEWAY),
    (PageReason.SCHEMA_VALIDATION_FAILED, status.HTTP_502_BAD_GATEWAY),
    (PageReason.RESPONSE_TRUNCATED, status.HTTP_502_BAD_GATEWAY),
    (PageReason.MODEL_REFUSAL, status.HTTP_502_BAD_GATEWAY),
    (PageReason.UNEXPECTED_TOOL_CALLS, status.HTTP_502_BAD_GATEWAY),
    (PageReason.UNEXPECTED_FINISH_REASON, status.HTTP_502_BAD_GATEWAY),
    (PageReason.PAGE_STATE_CHANGED, status.HTTP_409_CONFLICT),
)


def http_status_for_failed_run(response: ExtractionRunResponse) -> int:
    reasons = {p.reason for p in response.pages if p.status is PageStatus.FAILED and p.reason}
    for reason, code in FAILURE_PRECEDENCE:
        if reason in reasons:
            return code
    return status.HTTP_502_BAD_GATEWAY


def get_structured_extractor(settings: Settings = Depends(get_settings)) -> ExtractorLike:
    """Default extractor (reuses the existing Meta client factory lazily). Override in tests via
    ``app.dependency_overrides[get_structured_extractor]``. Callers cannot choose a model."""
    return StructuredExtractor(settings)


_RUN_FAILURE_RESPONSES = {
    code: {"model": ExtractionRunResponse, "description": desc}
    for code, desc in {
        409: "Page state changed during the run (full run summary in body)",
        429: "Model rate limited (full run summary in body)",
        502: "Model/API failure or unusable model output (full run summary in body)",
        503: "Extractor unavailable (full run summary in body)",
        504: "Model timeout (full run summary in body)",
    }.items()
}


@router.post(
    "/{document_id}/extract",
    dependencies=[Depends(require_workspace_write)],
    response_model=ExtractionRunResponse,
    summary="Run structured extraction on a document",
    description=(
        "Synchronously extracts facts and requirements from the document's canonical chunks "
        "using Muse Spark, verifies evidence deterministically and persists results as "
        "UNVERIFIED facts/links and DRAFT requirements. No request body.\n\n"
        "- Requires `processing_status == READY` (otherwise 409) and at least one eligible "
        "canonical chunk (otherwise 409).\n"
        "- One model call per eligible chunk. If that count exceeds `max_model_calls_per_request` "
        "(default 4) the request is rejected with 413 before any model call; exactly the "
        "configured number is allowed.\n"
        "- 200 for COMPLETED or PARTIAL runs. FAILED runs return a non-200 status with the full "
        "run summary as the body (429 rate limit, 504 timeout, 503 extractor unavailable, 502 "
        "other model failures, 409 page state changed).\n"
        "- The per-request cap is not rate limiting. There is no authentication yet; public "
        "deployment requires authentication, authorization and rate limiting."
    ),
    responses={
        404: {"description": "Document not found in workspace"},
        409: {"model": ExtractionRunResponse, "description": "Document not READY, no eligible chunks, or page state changed during the run"},
        413: {"description": "Eligible chunk count exceeds max_model_calls_per_request"},
        500: {"description": "Persistence failure; the whole run was rolled back"},
        **{k: v for k, v in _RUN_FAILURE_RESPONSES.items() if k != 409},
    },
)
def extract_document(
    workspace_id: uuid.UUID,
    document: Document = Depends(get_scoped_document),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    extractor: ExtractorLike = Depends(get_structured_extractor),
):
    if document.processing_status != DocumentProcessingStatus.READY.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document must be READY before structured extraction.",
        )

    eligible = count_eligible_chunks(session, workspace_id, document.id)
    if eligible == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document has no eligible canonical chunks to extract from.",
        )
    if eligible > settings.max_model_calls_per_request:
        metrics.record_metric(
            metrics.METRIC_USAGE_LIMIT_REJECTIONS, labels={"limit": "per_request"}
        )
        logger.warning(
            "extraction_rejected_over_call_cap",
            extra={
                "event": "usage.limit_rejected",
                "limit": "per_request",
                "eligible_chunks": eligible,
                "max_model_calls_per_request": settings.max_model_calls_per_request,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Extraction would require {eligible} model calls, which exceeds the "
                f"maximum of {settings.max_model_calls_per_request} per request."
            ),
        )

    # Only the default extractor needs a configured key; injected extractors (tests) skip this.
    if isinstance(extractor, StructuredExtractor) and not settings.model_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model service is not configured.",
        )

    try:
        result = run_document_extraction(
            session, workspace_id=workspace_id, document_id=document.id, extractor=extractor
        )
    except ModelBudgetExceededError as exc:
        # Rejected before any model request or write: the installation-wide daily budget for
        # this UTC day cannot cover the run.
        metrics.record_metric(
            metrics.METRIC_USAGE_LIMIT_REJECTIONS, labels={"limit": "daily"}
        )
        logger.warning(
            "daily_model_budget_exhausted",
            extra={
                "event": "usage.limit_rejected",
                "limit": "daily",
                "requested_calls": exc.requested,
                "remaining_calls": exc.remaining,
                "daily_model_call_limit": exc.limit,
                "budget_date": exc.budget_date.isoformat(),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Daily model call budget exhausted for "
                f"{exc.budget_date.isoformat()} (UTC): {exc.remaining} of {exc.limit} calls "
                f"remaining, {exc.requested} required. Nothing was extracted."
            ),
            headers={"Retry-After": str(seconds_until_next_utc_day())},
        ) from None
    except DocumentNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.") from None
    except ExtractionRunAbortedError:
        logger.error(
            "extraction_run_aborted_api",
            extra={"event": "api.extraction_aborted", "document_id": str(document.id)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Extraction failed and was rolled back.",
        ) from None

    body = ExtractionRunResponse.from_result(result)
    if result.status is RunStatus.FAILED:
        return JSONResponse(status_code=http_status_for_failed_run(body), content=body.model_dump(mode="json"))
    return body


def _evidence_by_target(rows, key: str) -> dict[uuid.UUID, list[EvidenceReferenceResponse]]:
    grouped: dict[uuid.UUID, list[EvidenceReferenceResponse]] = defaultdict(list)
    for row in rows:
        grouped[getattr(row, key)].append(
            EvidenceReferenceResponse(
                evidence_link_id=row.evidence_link_id,
                chunk_id=row.chunk_id,
                chunk_index=row.chunk_index,
                page_number=row.page_number,
                relationship_type=row.relationship_type,
                verification_status=row.verification_status,
            )
        )
    return grouped


@router.get(
    "/{document_id}/facts",
    response_model=PaginatedFactsResponse,
    summary="List extracted facts for a document",
    description=(
        "Facts for this workspace and document, ordered by page number (nulls last), creation "
        "time, then id. `page_number` comes from the related page and is null if the page no "
        "longer exists. Each fact carries `evidence` references (no chunk text). "
        "`normalized_value` excludes backend provenance, which is returned separately and "
        "allow-listed. Not access-controlled: authentication does not exist yet."
    ),
    responses={404: {"description": "Document not found in workspace"}},
)
def list_document_facts(
    workspace_id: uuid.UUID,
    document: Document = Depends(get_scoped_document),
    session: Session = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    fact_type: FactType | None = Query(None),
    verification_status: VerificationStatus | None = Query(None),
):
    rows, total = FactRepository(session).list_paginated_for_document(
        workspace_id,
        document.id,
        limit=limit,
        offset=offset,
        fact_type=fact_type.value if fact_type else None,
        verification_status=verification_status.value if verification_status else None,
    )
    evidence = (
        _evidence_by_target(
            EvidenceLinkRepository(session).list_references(
                workspace_id, document.id, fact_ids=[fact.id for fact, _ in rows]
            ),
            "fact_id",
        )
        if rows
        else {}
    )
    items = []
    for fact, page_number in rows:
        clean, backend = split_normalized_value(fact.normalized_value)
        items.append(
            ExtractedFactResponse(
                fact_id=fact.id,
                fact_type=fact.fact_type,
                raw_value=fact.raw_value,
                normalized_value=clean,
                source_quote=fact.source_quote,
                page_number=page_number,
                extraction_method=fact.extraction_method,
                verification_status=fact.verification_status,
                provenance=build_provenance(backend, include_review_status=False),
                evidence=evidence.get(fact.id, []),
                created_at=fact.created_at,
                updated_at=fact.updated_at,
            )
        )
    return PaginatedFactsResponse(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/{document_id}/requirements",
    response_model=PaginatedRequirementsResponse,
    summary="List requirements evidenced by a document",
    description=(
        "Requirements have no document column; they are returned when at least one of their "
        "evidence links points at a chunk of this document (workspace-scoped, de-duplicated, "
        "ordered by creation time then id). Each requirement appears once with an `evidence` "
        "array limited to this document. `review_status` and `source_quote` come from backend "
        "provenance; missing review metadata is never treated as approval. Not "
        "access-controlled: authentication does not exist yet."
    ),
    responses={404: {"description": "Document not found in workspace"}},
)
def list_document_requirements(
    workspace_id: uuid.UUID,
    document: Document = Depends(get_scoped_document),
    session: Session = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status_filter: RequirementStatus | None = Query(None, alias="status"),
):
    rows, total = RequirementRepository(session).list_paginated_for_document(
        workspace_id,
        document.id,
        limit=limit,
        offset=offset,
        status=status_filter.value if status_filter else None,
    )
    evidence = (
        _evidence_by_target(
            EvidenceLinkRepository(session).list_references(
                workspace_id, document.id, requirement_ids=[r.id for r in rows]
            ),
            "requirement_id",
        )
        if rows
        else {}
    )
    items = []
    for req in rows:
        clean, backend = split_normalized_value(req.normalized_value)
        quote = backend.get("source_quote")
        review = backend.get("review_status")
        items.append(
            RequirementResponse(
                requirement_id=req.id,
                title=req.title,
                description=req.description,
                requirement_type=req.requirement_type,
                normalized_value=clean,
                status=req.status,
                review_status=str(review) if review else None,
                source_quote=quote if isinstance(quote, str) else None,
                provenance=build_provenance(backend, include_review_status=True),
                evidence=evidence.get(req.id, []),
                created_at=req.created_at,
                updated_at=req.updated_at,
            )
        )
    return PaginatedRequirementsResponse(items=items, total=total, limit=limit, offset=offset)
