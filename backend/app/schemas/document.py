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
    """Parameters when triggering OCR or Vision transcription on a document."""

    page_numbers: list[int] | None = Field(
        default=None,
        description="Optional explicit 1-based page numbers to process. Defaults to all pages needing OCR.",
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
    provider: str | None = Field(
        default=None,
        description="Provider mode: 'tesseract', 'meta_vision', or 'compare'. Defaults to server setting.",
    )
    fallback_to_tesseract: bool | None = Field(
        default=None,
        description="When using meta_vision, whether to fallback to Tesseract on API failure.",
    )


class PageExtractionAttemptResponse(BaseModel):
    """Individual extraction attempt record for a page."""

    id: uuid.UUID
    page_number: int
    provider: str
    model_id: str | None = None
    prompt_version: str | None = None
    language_configuration: str | None = None
    extracted_text: str
    status: str
    latency_ms: int | None = None
    usage_metadata: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    created_at: datetime


class PageComparisonResponse(BaseModel):
    """Comparison of extraction results from different providers for a page."""

    page_number: int
    canonical_method: str
    canonical_text: str
    tesseract_attempt: PageExtractionAttemptResponse | None = None
    meta_vision_attempt: PageExtractionAttemptResponse | None = None


class PageOCRResponse(BaseModel):
    """Result of OCR/Vision processing for an individual page."""

    page_number: int
    char_count: int
    extraction_method: str
    extraction_status: str
    languages_used: str
    provider: str = "tesseract"
    error_message: str | None = None
    uncertain_regions: list[dict[str, Any]] = Field(default_factory=list)
    latency_ms: int | None = None


class OCRResponse(BaseModel):
    """Response returned after running OCR/Vision on a document."""

    document_id: uuid.UUID
    processing_status: str
    provider_mode: str
    pages_processed: int
    chunks_regenerated: int
    results: list[PageOCRResponse]
    comparisons: list[PageComparisonResponse] = Field(default_factory=list)
    message: str


class SelectCanonicalRequest(BaseModel):
    """Request payload to select canonical extraction for a page or document."""

    provider: str | None = Field(
        default=None,
        description="Provider to select as canonical: 'tesseract', 'meta_vision', or 'native_digital'.",
    )
    attempt_id: uuid.UUID | None = Field(
        default=None,
        description="Optional explicit extraction attempt ID to select as canonical.",
    )


class SelectCanonicalResponse(BaseModel):
    """Response returned after updating canonical extraction."""

    document_id: uuid.UUID
    pages_updated: int
    chunks_regenerated: int
    selected_provider: str
    message: str


