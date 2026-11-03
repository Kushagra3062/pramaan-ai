"""
Document API routes — upload, list, retrieve documents, pages, and chunks.

All routes are workspace-scoped under ``/api/v1/workspaces/{workspace_id}/documents``.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.auth import require_workspace_read, require_workspace_write
from app.core import metrics
from app.core.config import Settings, get_settings
from app.db.engine import get_db
from app.db.repositories.document_repo import (
    DocumentChunkRepository,
    DocumentPageRepository,
    DocumentRepository,
    PageExtractionAttemptRepository,
)
from app.models.enums import DocumentProcessingStatus
from app.schemas.document import (
    DocumentChunkResponse,
    DocumentPageResponse,
    DocumentResponse,
    DocumentUploadResponse,
    DuplicateDocumentResponse,
    OCRRequest,
    OCRResponse,
    PageComparisonResponse,
    PageExtractionAttemptResponse,
    PageOCRResponse,
    PaginatedChunksResponse,
    PaginatedDocumentsResponse,
    PaginatedPagesResponse,
    SelectCanonicalRequest,
    SelectCanonicalResponse,
)
from app.services.document_service import (
    DocumentNotFoundError,
    DocumentServiceError,
    DuplicateDocumentError,
    EvidenceLinksExistError,
    OCRExecutionError,
    ValidationError,
    WorkspaceNotFoundError,
    process_document_ocr,
    select_canonical_extraction,
    upload_document,
)
from app.services.model_budget import ModelBudgetExceededError, seconds_until_next_utc_day

router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/documents",
    tags=["documents"],
    dependencies=[Depends(require_workspace_read)],
)
logger = logging.getLogger(__name__)

EVIDENCE_LOCK_DETAIL = (
    "Canonical text cannot be regenerated or changed while evidence links exist for this document."
)


# ── Upload ────────────────────────────────────────────────────────────────────


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DocumentUploadResponse | DuplicateDocumentResponse,
    dependencies=[Depends(require_workspace_write)],
)
def upload(
    workspace_id: uuid.UUID,
    file: UploadFile,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Upload a PDF document to a workspace for processing."""
    try:
        file_data = file.file.read()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to read uploaded file.",
        ) from None

    try:
        doc = upload_document(
            session=session,
            settings=settings,
            workspace_id=workspace_id,
            filename=file.filename or "document.pdf",
            content_type=file.content_type,
            file_data=file_data,
        )
    except WorkspaceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace {workspace_id} not found.",
        ) from None
    except ValidationError as exc:
        metrics.record_metric(metrics.METRIC_DOCUMENT_UPLOADS, labels={"outcome": "rejected"})
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from None
    except DuplicateDocumentError as exc:
        metrics.record_metric(metrics.METRIC_DOCUMENT_UPLOADS, labels={"outcome": "duplicate"})
        return DuplicateDocumentResponse(
            existing_document_id=exc.existing_document.id,
            processing_status=exc.existing_document.processing_status,
        )
    except DocumentServiceError as exc:
        metrics.record_metric(metrics.METRIC_DOCUMENT_UPLOADS, labels={"outcome": "failure"})
        logger.error(
            "document_upload_failed",
            extra={"event": "api.document_upload_failed", "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document processing failed.",
        ) from None

    metrics.record_metric(
        metrics.METRIC_DOCUMENT_UPLOADS,
        labels={
            "outcome": (
                "success"
                if doc.processing_status != DocumentProcessingStatus.FAILED.value
                else "failed"
            )
        },
    )
    return DocumentUploadResponse(
        id=doc.id,
        processing_status=doc.processing_status,
    )


# ── List documents ────────────────────────────────────────────────────────────


@router.get("", response_model=PaginatedDocumentsResponse)
def list_documents(
    workspace_id: uuid.UUID,
    status_filter: str | None = None,
    limit: int = 20,
    offset: int = 0,
    session: Session = Depends(get_db),
):
    """List documents within a workspace with optional status filtering."""
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)

    doc_repo = DocumentRepository(session)
    documents = doc_repo.list_by_workspace(
        workspace_id=workspace_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    total = doc_repo.count_by_workspace(workspace_id=workspace_id, status=status_filter)

    return PaginatedDocumentsResponse(
        items=[DocumentResponse.model_validate(d) for d in documents],
        total=total,
        limit=limit,
        offset=offset,
    )


# ── Get single document ──────────────────────────────────────────────────────


@router.get("/{document_id}", response_model=DocumentResponse)
def get_document(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    session: Session = Depends(get_db),
):
    """Retrieve metadata and processing status for a specific document."""
    doc_repo = DocumentRepository(session)
    doc = doc_repo.get_by_id(document_id)

    if doc is None or doc.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )

    return DocumentResponse.model_validate(doc)


# ── Get document pages ────────────────────────────────────────────────────────


@router.get("/{document_id}/pages", response_model=PaginatedPagesResponse)
def get_document_pages(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    session: Session = Depends(get_db),
):
    """Retrieve extracted pages for a document."""
    doc_repo = DocumentRepository(session)
    doc = doc_repo.get_by_id(document_id)

    if doc is None or doc.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )

    page_repo = DocumentPageRepository(session)
    pages = page_repo.list_by_document(document_id)

    return PaginatedPagesResponse(
        items=[DocumentPageResponse.model_validate(p) for p in pages],
        total=len(pages),
    )


# ── Get document chunks ──────────────────────────────────────────────────────


@router.get("/{document_id}/chunks", response_model=PaginatedChunksResponse)
def get_document_chunks(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    session: Session = Depends(get_db),
):
    """Retrieve text chunks for a document."""
    doc_repo = DocumentRepository(session)
    doc = doc_repo.get_by_id(document_id)

    if doc is None or doc.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )

    chunk_repo = DocumentChunkRepository(session)
    chunks = chunk_repo.list_by_document(document_id)

    return PaginatedChunksResponse(
        items=[DocumentChunkResponse.model_validate(c) for c in chunks],
        total=len(chunks),
    )


# ── Trigger OCR / Vision ──────────────────────────────────────────────────────


def _build_comparisons(
    pages: list,
    attempts: list,
) -> list[PageComparisonResponse]:
    """Helper to group extraction attempts by page into comparison records."""
    attempts_by_page: dict[int, dict[str, PageExtractionAttemptResponse]] = {}
    for att in sorted(attempts, key=lambda a: a.created_at):
        att_resp = PageExtractionAttemptResponse(
            id=att.id,
            page_number=att.page_number,
            provider=att.provider,
            model_id=att.model_id,
            prompt_version=att.prompt_version,
            language_configuration=att.language_configuration,
            extracted_text=att.extracted_text,
            status=att.status,
            latency_ms=att.latency_ms,
            usage_metadata=att.usage_metadata or {},
            error_code=att.error_code,
            created_at=att.created_at,
        )
        if att.page_number not in attempts_by_page:
            attempts_by_page[att.page_number] = {}
        # Keep latest attempt per provider for this page
        attempts_by_page[att.page_number][att.provider] = att_resp

    comparisons: list[PageComparisonResponse] = []
    for p in sorted(pages, key=lambda p: p.page_number):
        page_attempts = attempts_by_page.get(p.page_number, {})
        comparisons.append(
            PageComparisonResponse(
                page_number=p.page_number,
                canonical_method=p.extraction_method,
                canonical_text=p.extracted_text or "",
                tesseract_attempt=page_attempts.get("tesseract"),
                meta_vision_attempt=page_attempts.get("meta_vision"),
            )
        )
    return comparisons


@router.post("/{document_id}/ocr", response_model=OCRResponse, dependencies=[Depends(require_workspace_write)])
def trigger_ocr(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    request: OCRRequest | None = None,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """
    Trigger OCR or Vision processing on selected pages of a document.

    If page_numbers is not provided, defaults to all pages needing OCR.
    Supports provider: 'tesseract', 'meta_vision', or 'compare'.
    """
    req = request or OCRRequest()

    try:
        doc, results, regenerated_count, provider_mode = process_document_ocr(
            session=session,
            settings=settings,
            workspace_id=workspace_id,
            document_id=document_id,
            page_numbers=req.page_numbers,
            languages=req.languages,
            dpi=req.dpi,
            provider=req.provider,
            fallback_to_tesseract=req.fallback_to_tesseract,
        )
    except ModelBudgetExceededError as exc:
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
                f"remaining, {exc.requested} required. No page was processed."
            ),
            headers={"Retry-After": str(seconds_until_next_utc_day())},
        ) from None
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        ) from None
    except ValidationError as exc:
        metrics.record_metric(metrics.METRIC_OCR_PAGES, labels={"outcome": "rejected"})
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from None
    except EvidenceLinksExistError:
        metrics.record_metric(metrics.METRIC_OCR_PAGES, labels={"outcome": "blocked"})
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=EVIDENCE_LOCK_DETAIL,
        ) from None
    except OCRExecutionError as exc:
        metrics.record_metric(metrics.METRIC_OCR_PAGES, labels={"outcome": "failure"})
        logger.error(
            "ocr_execution_error",
            extra={"event": "api.ocr_failed", "document_id": str(document_id), "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from None
    except Exception:
        metrics.record_metric(metrics.METRIC_OCR_PAGES, labels={"outcome": "failure"})
        logger.exception(
            "ocr_unexpected_error",
            extra={"event": "api.ocr_unexpected_failed", "document_id": str(document_id)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OCR processing failed unexpectedly.",
        ) from None

    comparisons: list[PageComparisonResponse] = []
    if provider_mode == "compare":
        page_repo = DocumentPageRepository(session)
        attempt_repo = PageExtractionAttemptRepository(session)
        pages = page_repo.list_by_document(document_id)
        attempts = attempt_repo.list_by_document(document_id)
        comparisons = _build_comparisons(pages, attempts)

    metrics.record_metric(
        metrics.METRIC_OCR_PAGES,
        value=len(results),
        unit="pages",
        labels={"outcome": "success", "mode": provider_mode},
    )
    return OCRResponse(
        document_id=doc.id,
        processing_status=doc.processing_status,
        provider_mode=provider_mode,
        pages_processed=len(results),
        chunks_regenerated=regenerated_count,
        results=[
            PageOCRResponse(
                page_number=r.page_number,
                char_count=r.char_count,
                extraction_method=r.extraction_method,
                extraction_status=r.extraction_status,
                languages_used=r.languages_used,
                provider=r.provider,
                error_message=r.error_message,
                uncertain_regions=r.uncertain_regions,
                latency_ms=r.latency_ms,
            )
            for r in results
        ],
        comparisons=comparisons,
        message="OCR processing completed.",
    )


# ── Extraction Attempts & Comparison ─────────────────────────────────────────


@router.get("/{document_id}/extraction-attempts", response_model=list[PageExtractionAttemptResponse])
def get_extraction_attempts(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    page_number: int | None = None,
    session: Session = Depends(get_db),
):
    """Retrieve extraction attempts for a document or specific page."""
    doc_repo = DocumentRepository(session)
    doc = doc_repo.get_by_id(document_id)
    if doc is None or doc.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    attempt_repo = PageExtractionAttemptRepository(session)
    attempts = attempt_repo.list_by_document(document_id)
    if page_number is not None:
        attempts = [a for a in attempts if a.page_number == page_number]

    return [
        PageExtractionAttemptResponse(
            id=a.id,
            page_number=a.page_number,
            provider=a.provider,
            model_id=a.model_id,
            prompt_version=a.prompt_version,
            language_configuration=a.language_configuration,
            extracted_text=a.extracted_text,
            status=a.status,
            latency_ms=a.latency_ms,
            usage_metadata=a.usage_metadata or {},
            error_code=a.error_code,
            created_at=a.created_at,
        )
        for a in attempts
    ]


@router.get("/{document_id}/compare", response_model=list[PageComparisonResponse])
def get_document_comparison(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    session: Session = Depends(get_db),
):
    """Retrieve side-by-side comparison of extraction providers across pages."""
    doc_repo = DocumentRepository(session)
    doc = doc_repo.get_by_id(document_id)
    if doc is None or doc.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    page_repo = DocumentPageRepository(session)
    attempt_repo = PageExtractionAttemptRepository(session)
    pages = page_repo.list_by_document(document_id)
    attempts = attempt_repo.list_by_document(document_id)

    return _build_comparisons(pages, attempts)


# ── Canonical Extraction Selection ──────────────────────────────────────────


@router.put("/{document_id}/pages/{page_number}/canonical", response_model=SelectCanonicalResponse, dependencies=[Depends(require_workspace_write)])
def set_page_canonical_extraction(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    page_number: int,
    payload: SelectCanonicalRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Explicitly select the authoritative canonical extraction for a specific page."""
    try:
        doc, updated_pages, chunks_count = select_canonical_extraction(
            session=session,
            settings=settings,
            workspace_id=workspace_id,
            document_id=document_id,
            page_number=page_number,
            provider=payload.provider,
            attempt_id=payload.attempt_id,
        )
    except DocumentNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.") from None
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from None
    except EvidenceLinksExistError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=EVIDENCE_LOCK_DETAIL,
        ) from None
    except Exception as exc:
        logger.error("set_canonical_failed", extra={"error": str(exc), "document_id": str(document_id)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update canonical extraction.",
        ) from None

    selected_prov = payload.provider or (str(payload.attempt_id) if payload.attempt_id else "unknown")
    return SelectCanonicalResponse(
        document_id=doc.id,
        pages_updated=len(updated_pages),
        chunks_regenerated=chunks_count,
        selected_provider=selected_prov,
        message=f"Canonical extraction set to {selected_prov} for page {page_number}.",
    )


@router.put("/{document_id}/canonical", response_model=SelectCanonicalResponse, dependencies=[Depends(require_workspace_write)])
def set_document_canonical_extraction(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    payload: SelectCanonicalRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Explicitly select the authoritative canonical extraction across all pages of a document."""
    try:
        doc, updated_pages, chunks_count = select_canonical_extraction(
            session=session,
            settings=settings,
            workspace_id=workspace_id,
            document_id=document_id,
            page_number=None,
            provider=payload.provider,
            attempt_id=payload.attempt_id,
        )
    except DocumentNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.") from None
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from None
    except EvidenceLinksExistError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=EVIDENCE_LOCK_DETAIL,
        ) from None
    except Exception as exc:
        logger.error("set_canonical_failed", extra={"error": str(exc), "document_id": str(document_id)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update canonical extraction.",
        ) from None

    selected_prov = payload.provider or (str(payload.attempt_id) if payload.attempt_id else "unknown")
    return SelectCanonicalResponse(
        document_id=doc.id,
        pages_updated=len(updated_pages),
        chunks_regenerated=chunks_count,
        selected_provider=selected_prov,
        message=f"Canonical extraction set to {selected_prov} for all document pages.",
    )



