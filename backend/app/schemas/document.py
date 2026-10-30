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

