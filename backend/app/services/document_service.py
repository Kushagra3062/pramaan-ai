"""
Document processing service — orchestrates upload, extraction, and chunking.

This service implements the document processing lifecycle:
  UPLOADED → VALIDATING → PROCESSING → READY | NEEDS_OCR | FAILED

It coordinates storage, extraction, and chunking while ensuring database
consistency and proper cleanup on failure.
"""

from __future__ import annotations

import hashlib
import logging
import uuid

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.repositories.document_repo import (
    DocumentChunkRepository,
    DocumentPageRepository,
    DocumentRepository,
)
from app.db.repositories.workspace_repo import WorkspaceRepository
from app.ingestion.chunking import PageText, chunk_document
from app.ingestion.extraction import (
    PDFExtractionError,
    extract_pdf,
    validate_pdf_signature,
)
from app.ingestion.ocr import (
    PageOCRResult,
    TesseractLanguageNotFoundError,
    TesseractNotFoundError,
    perform_document_ocr,
)
from app.ingestion.storage import LocalStorageAdapter, StorageAdapter, StorageError
from app.models.document import Document
from app.models.enums import DocumentProcessingStatus

logger = logging.getLogger(__name__)

# Allowed MIME types for upload
ALLOWED_MIME_TYPES = {"application/pdf"}
ALLOWED_EXTENSIONS = {".pdf"}


class DocumentServiceError(Exception):
    """Base exception for document service errors."""


class ValidationError(DocumentServiceError):
    """Input validation failure."""


class DuplicateDocumentError(DocumentServiceError):
    """A document with the same SHA-256 already exists in the workspace."""

    def __init__(self, existing_document: Document) -> None:
        self.existing_document = existing_document
        super().__init__("Duplicate document")


class WorkspaceNotFoundError(DocumentServiceError):
    """The requested workspace does not exist."""


class DocumentNotFoundError(DocumentServiceError):
    """The requested document does not exist."""


class OCRExecutionError(DocumentServiceError):
    """Failure during OCR processing."""


def _get_storage(settings: Settings) -> StorageAdapter:
    """Create a storage adapter from settings."""
    return LocalStorageAdapter(settings.document_storage_dir)


def upload_document(
    *,
    session: Session,
    settings: Settings,
    workspace_id: uuid.UUID,
    filename: str,
    content_type: str | None,
    file_data: bytes,
) -> Document:
    """
    Upload and process a PDF document.

    Steps:
    1. Validate workspace exists
    2. Validate file (extension, content type, signature, size)
    3. Compute SHA-256 and check for duplicates
    4. Store file privately
    5. Extract text from pages
    6. Create chunks
    7. Persist everything in a transaction

    Returns the created Document record.
    Raises DocumentServiceError subclasses on failure.
    """
    # 1. Validate workspace
    ws_repo = WorkspaceRepository(session)
    workspace = ws_repo.get_by_id(workspace_id)
    if workspace is None:
        raise WorkspaceNotFoundError(f"Workspace {workspace_id} not found")

    # 2. Validate file
    _validate_file(filename=filename, content_type=content_type, file_data=file_data, settings=settings)

    # 3. Compute SHA-256 and check for duplicates
    sha256 = hashlib.sha256(file_data).hexdigest()
    doc_repo = DocumentRepository(session)
    existing = doc_repo.get_by_sha256(workspace_id, sha256)
    if existing is not None:
        raise DuplicateDocumentError(existing)

    # 4. Store file privately
    storage = _get_storage(settings)
    document_id = uuid.uuid4()
    storage_key: str | None = None

    try:
        storage_key = storage.save(
            workspace_id=workspace_id,
            document_id=document_id,
            data=file_data,
            suffix="pdf",
        )
    except StorageError as exc:
        logger.error(
            "storage_save_failed",
            extra={"event": "document.storage_failed", "error": str(exc)},
        )
        raise DocumentServiceError("Failed to store document") from exc

    try:
        # 5. Create document record (status: UPLOADED)
        doc = Document(
            id=document_id,
            workspace_id=workspace_id,
            original_filename=_safe_filename(filename),
            storage_key=storage_key,
            sha256=sha256,
            mime_type="application/pdf",
            file_size_bytes=len(file_data),
            processing_status=DocumentProcessingStatus.UPLOADED.value,
        )
        session.add(doc)
        session.flush()

        # 6. Transition to PROCESSING
        doc.processing_status = DocumentProcessingStatus.PROCESSING.value
        session.flush()

        # 7. Extract text
        try:
            extraction_result = extract_pdf(
                file_data,
                min_text_chars=settings.min_text_density_chars,
            )
        except PDFExtractionError as exc:
            doc.processing_status = DocumentProcessingStatus.FAILED.value
            doc.error_details = {"stage": "extraction", "error": str(exc)}
            session.flush()
            session.commit()
            logger.warning(
                "pdf_extraction_failed",
                extra={
                    "event": "document.extraction_failed",
                    "document_id": str(document_id),
                    "error": str(exc),
                },
            )
            return doc

        doc.page_count = extraction_result.page_count

        # 8. Persist pages
        page_repo = DocumentPageRepository(session)
        for page_result in extraction_result.pages:
            page_repo.create(
                document_id=document_id,
                page_number=page_result.page_number,
                extracted_text=page_result.text,
                extraction_method=page_result.extraction_method,
                extraction_status=page_result.extraction_status,
                page_metadata=page_result.metadata,
            )

        # 9. Create chunks from pages with text
        pages_with_text = [
            PageText(page_number=p.page_number, text=p.text)
            for p in extraction_result.pages
            if p.char_count >= settings.min_text_density_chars
        ]

        chunk_results = chunk_document(
            pages_with_text,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )

        chunk_repo = DocumentChunkRepository(session)
        for chunk in chunk_results:
            chunk_repo.create(
                workspace_id=workspace_id,
                document_id=document_id,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                embedding_model=settings.embedding_model_name,
                embedding_version=settings.embedding_version,
                content_hash=chunk.content_hash,
                token_count=chunk.token_count,
            )

        # 10. Set final status
        if extraction_result.overall_status == "NEEDS_OCR":
            doc.processing_status = DocumentProcessingStatus.NEEDS_OCR.value
        else:
            doc.processing_status = DocumentProcessingStatus.READY.value

        session.flush()
        session.commit()

        logger.info(
            "document_processing_completed",
            extra={
                "event": "document.processing_completed",
                "document_id": str(document_id),
                "status": doc.processing_status,
                "page_count": doc.page_count,
                "chunk_count": len(chunk_results),
            },
        )
        return doc

    except Exception:
        session.rollback()
        # Cleanup stored file on DB failure
        if storage_key:
            try:
                storage.delete(storage_key)
                logger.info(
                    "orphan_file_cleaned",
                    extra={
                        "event": "document.orphan_cleaned",
                        "storage_key": storage_key,
                    },
                )
            except Exception:
                logger.error(
                    "orphan_cleanup_failed",
                    extra={
                        "event": "document.orphan_cleanup_failed",
                        "storage_key": storage_key,
                    },
                )
        raise


def _validate_file(
    *,
    filename: str,
    content_type: str | None,
    file_data: bytes,
    settings: Settings,
) -> None:
    """Validate uploaded file against safety rules."""
    # Extension check
    lower_name = filename.lower()
    if not any(lower_name.endswith(ext) for ext in ALLOWED_EXTENSIONS):
        raise ValidationError(f"File extension not allowed. Accepted: {', '.join(ALLOWED_EXTENSIONS)}")

    # Content-type check
    if content_type and content_type not in ALLOWED_MIME_TYPES:
        raise ValidationError(f"Content type '{content_type}' not allowed. Accepted: application/pdf")

    # Size check
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(file_data) > max_bytes:
        raise ValidationError(f"File size exceeds maximum of {settings.max_upload_mb} MB")

    if len(file_data) == 0:
        raise ValidationError("Uploaded file is empty")

    # PDF signature check
    if not validate_pdf_signature(file_data):
        raise ValidationError("File does not have a valid PDF signature")


def _safe_filename(filename: str) -> str:
    """Sanitize a user-supplied filename for storage as metadata."""
    import os

    # Take only the basename to prevent path traversal
    name = os.path.basename(filename)
    # Remove any null bytes or control characters
    name = "".join(c for c in name if c.isprintable() and c not in ("\x00", "/", "\\"))
    return name[:255] if name else "document.pdf"


def process_document_ocr(
    *,
    session: Session,
    settings: Settings,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    page_numbers: list[int] | None = None,
    languages: str | None = None,
    dpi: int | None = None,
) -> tuple[Document, list[PageOCRResult], int]:
    """
    Run OCR on selected pages of an existing document and regenerate chunks.

    Returns:
        (updated_document, list_of_page_results, chunks_regenerated_count)
    """
    doc_repo = DocumentRepository(session)
    doc = doc_repo.get_by_id(document_id)

    if doc is None or doc.workspace_id != workspace_id:
        raise DocumentNotFoundError(f"Document {document_id} not found in workspace")

    page_repo = DocumentPageRepository(session)
    existing_pages = page_repo.list_by_document(document_id)
    if not existing_pages:
        raise ValidationError("Document has no recorded pages to OCR")

    page_map = {p.page_number: p for p in existing_pages}

    # Determine pages to OCR
    if page_numbers is not None:
        if not page_numbers:
            raise ValidationError("page_numbers list cannot be empty when provided")
        for num in page_numbers:
            if num not in page_map:
                raise ValidationError(f"Invalid page number {num}; valid range is 1 to {len(existing_pages)}")
        targets = sorted(set(page_numbers))
    else:
        # Default: all pages marked as NEEDS_OCR or with extraction_status != 'COMPLETED'
        targets = [
            p.page_number
            for p in existing_pages
            if p.extraction_status in ("NEEDS_OCR", "FAILED")
            or p.extraction_method == "needs_ocr"
        ]
        if not targets:
            # If all are already completed, nothing to do
            return doc, [], 0

    # Limit check
    max_pages = settings.ocr_max_pages_per_request
    if len(targets) > max_pages:
        raise ValidationError(
            f"Requested {len(targets)} pages for OCR, which exceeds maximum limit of {max_pages}"
        )

    # Load stored PDF bytes
    storage = _get_storage(settings)
    try:
        pdf_bytes = storage.load(doc.storage_key)
    except StorageError as exc:
        raise DocumentServiceError(f"Cannot load stored document: {exc}") from exc

    selected_langs = languages or settings.ocr_languages
    selected_dpi = dpi or settings.ocr_dpi

    existing_texts = {p.page_number: p.extracted_text or "" for p in existing_pages}

    try:
        ocr_results = perform_document_ocr(
            pdf_bytes,
            targets,
            languages=selected_langs,
            dpi=selected_dpi,
            existing_pages=existing_texts,
            tessdata_prefix=settings.tessdata_prefix,
        )
    except (TesseractNotFoundError, TesseractLanguageNotFoundError) as exc:
        raise OCRExecutionError(str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "ocr_processing_failed",
            extra={
                "event": "ocr.processing_failed",
                "document_id": str(document_id),
                "error": str(exc),
            },
        )
        raise OCRExecutionError(f"OCR failed: {exc}") from exc

    # Update database transactionally
    try:
        # Update each page
        for res in ocr_results:
            page = page_map[res.page_number]
            page.extracted_text = res.recognized_text
            page.extraction_method = res.extraction_method
            page.extraction_status = res.extraction_status

            meta = dict(page.page_metadata or {})
            meta.update(res.metadata)
            meta["ocr_languages"] = res.languages_used
            if res.error_message:
                meta["ocr_error"] = res.error_message
            page.page_metadata = meta

        session.flush()

        # Regenerate all chunks for the document to guarantee consistent chunk index & boundaries
        chunk_repo = DocumentChunkRepository(session)
        chunk_repo.delete_by_document(document_id)

        all_pages_now = page_repo.list_by_document(document_id)
        pages_with_text = [
            PageText(page_number=p.page_number, text=p.extracted_text)
            for p in all_pages_now
            if p.extracted_text and len(p.extracted_text.strip()) >= settings.min_text_density_chars
        ]

        new_chunks = chunk_document(
            pages_with_text,
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )

        for chunk in new_chunks:
            chunk_repo.create(
                workspace_id=workspace_id,
                document_id=document_id,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                embedding_model=settings.embedding_model_name,
                embedding_version=settings.embedding_version,
                content_hash=chunk.content_hash,
                token_count=chunk.token_count,
            )

        # Re-assess document status
        any_needs_ocr = any(p.extraction_status == "NEEDS_OCR" for p in all_pages_now)
        any_failed = any(p.extraction_status == "FAILED" for p in all_pages_now)

        if any_failed:
            doc.processing_status = DocumentProcessingStatus.FAILED.value
        elif any_needs_ocr:
            doc.processing_status = DocumentProcessingStatus.NEEDS_OCR.value
        else:
            doc.processing_status = DocumentProcessingStatus.READY.value

        session.flush()
        session.commit()

        logger.info(
            "document_ocr_completed",
            extra={
                "event": "document.ocr_completed",
                "document_id": str(document_id),
                "pages_ocr": len(ocr_results),
                "new_chunks_count": len(new_chunks),
                "final_status": doc.processing_status,
            },
        )
        return doc, ocr_results, len(new_chunks)

    except Exception:
        session.rollback()
        raise

