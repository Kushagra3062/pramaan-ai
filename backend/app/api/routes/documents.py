"""
Document API routes — upload, list, retrieve documents, pages, and chunks.

All routes are workspace-scoped under ``/api/v1/workspaces/{workspace_id}/documents``.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.engine import get_db
from app.db.repositories.document_repo import (
    DocumentChunkRepository,
    DocumentPageRepository,
    DocumentRepository,
)
from app.schemas.document import (
    DocumentChunkResponse,
    DocumentPageResponse,
    DocumentResponse,
    DocumentUploadResponse,
    DuplicateDocumentResponse,
    OCRRequest,
    OCRResponse,
    PageOCRResponse,
    PaginatedChunksResponse,
    PaginatedDocumentsResponse,
    PaginatedPagesResponse,
)
from app.services.document_service import (
    DocumentNotFoundError,
    DocumentServiceError,
    DuplicateDocumentError,
    OCRExecutionError,
    ValidationError,
    WorkspaceNotFoundError,
    process_document_ocr,
    upload_document,
)

router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/documents",
    tags=["documents"],
)
logger = logging.getLogger(__name__)


# ── Upload ────────────────────────────────────────────────────────────────────


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DocumentUploadResponse | DuplicateDocumentResponse,
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
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from None
    except DuplicateDocumentError as exc:
        return DuplicateDocumentResponse(
            existing_document_id=exc.existing_document.id,
            processing_status=exc.existing_document.processing_status,
        )
    except DocumentServiceError as exc:
        logger.error(
            "document_upload_failed",
            extra={"event": "api.document_upload_failed", "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document processing failed.",
        ) from None

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


# ── Trigger OCR ──────────────────────────────────────────────────────────────


@router.post("/{document_id}/ocr", response_model=OCRResponse)
def trigger_ocr(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    request: OCRRequest | None = None,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """
    Trigger OCR processing on selected pages of a document.

    If page_numbers is not provided, defaults to all pages needing OCR.
    """
    req = request or OCRRequest()

    try:
        doc, results, regenerated_count = process_document_ocr(
            session=session,
            settings=settings,
            workspace_id=workspace_id,
            document_id=document_id,
            page_numbers=req.page_numbers,
            languages=req.languages,
            dpi=req.dpi,
        )
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        ) from None
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from None
    except OCRExecutionError as exc:
        logger.error(
            "ocr_execution_error",
            extra={"event": "api.ocr_failed", "document_id": str(document_id), "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from None
    except Exception:
        logger.exception(
            "ocr_unexpected_error",
            extra={"event": "api.ocr_unexpected_failed", "document_id": str(document_id)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OCR processing failed unexpectedly.",
        ) from None

    return OCRResponse(
        document_id=doc.id,
        processing_status=doc.processing_status,
        pages_processed=len(results),
        chunks_regenerated=regenerated_count,
        results=[
            PageOCRResponse(
                page_number=r.page_number,
                char_count=r.char_count,
                extraction_method=r.extraction_method,
                extraction_status=r.extraction_status,
                languages_used=r.languages_used,
                error_message=r.error_message,
            )
            for r in results
        ],
        message="OCR processing completed.",
    )


