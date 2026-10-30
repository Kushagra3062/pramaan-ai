"""Pydantic schemas for the document API."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ── Response schemas ──────────────────────────────────────────────────────────


class DocumentResponse(BaseModel):
    """Compact document metadata returned by upload, list, and get endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    original_filename: str
    mime_type: str
    file_size_bytes: int
    sha256: str
    page_count: int | None = None
    detected_language: str | None = None
    processing_status: str
    error_details: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class DocumentUploadResponse(BaseModel):
    """Response after a successful upload request."""

    id: uuid.UUID
    processing_status: str
    message: str = "Document uploaded and processing started."


class DocumentPageResponse(BaseModel):
    """Single page extracted from a document."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    page_number: int
    extracted_text: str | None = None
    extraction_method: str
    extraction_status: str
    page_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class DocumentChunkResponse(BaseModel):
    """Single chunk created from document text."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    document_id: uuid.UUID
    page_start: int
    page_end: int
    chunk_index: int
    content: str
    token_count: int | None = None
    content_hash: str
    created_at: datetime


class PaginatedDocumentsResponse(BaseModel):
    """Paginated list of documents."""

    items: list[DocumentResponse]
    total: int
    limit: int
    offset: int


class PaginatedPagesResponse(BaseModel):
    """Paginated list of document pages."""

    items: list[DocumentPageResponse]
    total: int


class PaginatedChunksResponse(BaseModel):
    """Paginated list of document chunks."""

    items: list[DocumentChunkResponse]
    total: int


class DuplicateDocumentResponse(BaseModel):
    """Returned when an identical document already exists in the workspace."""

    existing_document_id: uuid.UUID
    processing_status: str
    message: str = "A document with the same content already exists in this workspace."


# ── OCR schemas ──────────────────────────────────────────────────────────────


class OCRRequest(BaseModel):
    """Optional parameters when triggering OCR on a document."""

    page_numbers: list[int] | None = Field(
        default=None,
        description="Optional explicit 1-based page numbers to OCR. Defaults to all pages needing OCR.",
    )
    languages: str | None = Field(
        default=None,
        description="Optional override for OCR languages (e.g. 'eng+mal', 'hin').",
    )
    dpi: int | None = Field(
        default=None,
        ge=72,
        le=600,
        description="Optional rendering DPI override.",
    )


class PageOCRResponse(BaseModel):
    """Result of OCR processing for an individual page."""

    page_number: int
    char_count: int
    extraction_method: str
    extraction_status: str
    languages_used: str
    error_message: str | None = None


class OCRResponse(BaseModel):
    """Response returned after running OCR on a document."""

    document_id: uuid.UUID
    processing_status: str
    pages_processed: int
    chunks_regenerated: int
    results: list[PageOCRResponse]
    message: str

