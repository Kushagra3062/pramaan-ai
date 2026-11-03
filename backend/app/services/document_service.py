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
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.repositories.document_repo import (
    DocumentChunkRepository,
    DocumentPageRepository,
    DocumentRepository,
    PageExtractionAttemptRepository,
)
from app.db.repositories.fact_repo import EvidenceLinkRepository
from app.db.repositories.workspace_repo import WorkspaceRepository
from app.ingestion.chunking import PageText, chunk_document, clean_canonical_text
from app.ingestion.extraction import (
    PDFExtractionError,
    extract_pdf,
    validate_pdf_signature,
)
from app.ingestion.meta_vision import MetaVisionExtractor
from app.ingestion.ocr import (
    PageOCRResult,
    TesseractLanguageNotFoundError,
    TesseractNotFoundError,
    perform_document_ocr,
    render_page_to_image_bytes,
    run_ocr_on_page,
)
from app.ingestion.storage import (
    GCSStorageAdapter,
    LocalStorageAdapter,
    StorageAdapter,
    StorageError,
    SupabaseStorageAdapter,
)
from app.models.document import Document, DocumentPage
from app.models.enums import DocumentProcessingStatus
from app.services.model_budget import get_model_budget

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


class EvidenceLinksExistError(DocumentServiceError):
    """Chunk regeneration refused: evidence links reference this document's chunks.

    Regenerating chunks deletes and re-creates them with new IDs, which would cascade-delete
    evidence links (including human-verified ones). Nothing is modified when this is raised.
    """

    def __init__(self, document_id: uuid.UUID) -> None:
        self.document_id = document_id
        super().__init__(
            "Cannot regenerate chunks: evidence links reference existing chunks of this document"
        )


class OCRExecutionError(DocumentServiceError):
    """Failure during OCR processing."""


def _get_storage(settings: Settings) -> StorageAdapter:
    """Create a storage adapter from settings."""
    if settings.storage_backend == "gcs":
        if not settings.gcs_bucket_name:
            raise DocumentServiceError("GCS_BUCKET_NAME is required when STORAGE_BACKEND=gcs")
        return GCSStorageAdapter(settings.gcs_bucket_name, settings.gcs_project_id)
    if settings.storage_backend == "supabase":
        if not settings.supabase_url or not settings.supabase_service_role_key or not settings.supabase_storage_bucket:
            raise DocumentServiceError("Supabase Storage configuration is incomplete")
        return SupabaseStorageAdapter(settings.supabase_url, settings.supabase_service_role_key, settings.supabase_storage_bucket)
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

        # 8. Persist pages and initial digital extraction attempts
        page_repo = DocumentPageRepository(session)
        attempt_repo = PageExtractionAttemptRepository(session)
        for page_result in extraction_result.pages:
            meta = dict(page_result.metadata or {})
            is_digital = page_result.extraction_method in ("native_pdf", "DIGITAL_TEXT") and bool(page_result.text)
            if is_digital:
                meta["native_digital_text"] = page_result.text
                meta["canonical_provider"] = "native_digital"

            page_obj = page_repo.create(
                document_id=document_id,
                page_number=page_result.page_number,
                extracted_text=page_result.text,
                extraction_method=page_result.extraction_method,
                extraction_status=page_result.extraction_status,
                page_metadata=meta,
            )

            if is_digital:
                attempt_repo.record_attempt(
                    document_id=document_id,
                    document_page_id=page_obj.id,
                    page_number=page_result.page_number,
                    provider="native_digital",
                    extracted_text=page_result.text,
                    status="COMPLETED",
                )

        # 9. Create chunks from canonical page records using shared helper
        chunk_count = generate_and_persist_canonical_chunks(
            session=session,
            settings=settings,
            workspace_id=workspace_id,
            document_id=document_id,
        )

        session.flush()
        session.commit()

        logger.info(
            "document_processing_completed",
            extra={
                "event": "document.processing_completed",
                "document_id": str(document_id),
                "status": doc.processing_status,
                "page_count": doc.page_count,
                "chunk_count": chunk_count,
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


def generate_and_persist_canonical_chunks(
    *,
    session: Session,
    settings: Settings,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
) -> int:
    """
    Safely and transactionally generate chunks for a document exclusively from canonical page text.
    Shared implementation used by both initial document upload and chunk regeneration.

    1. Reads all DocumentPage records for document_id from database.
    2. Builds PageText inputs using clean_canonical_text(page.extracted_text)
       for pages where extraction_status is COMPLETED and canonical text exists.
    3. Locks the document row and refuses (EvidenceLinksExistError, nothing modified) if any
       evidence link references its chunks; otherwise deletes existing chunks.
    4. Runs deterministic, page-aware chunk_document().
    5. Persists new ChunkResult records with exact page provenance (page_start == page_end).
    6. Re-assesses document processing status (READY, NEEDS_OCR, FAILED).

    Returns count of chunks generated.
    """
    page_repo = DocumentPageRepository(session)
    chunk_repo = DocumentChunkRepository(session)
    doc_repo = DocumentRepository(session)
    # Lock the parent document row (held until the outer transaction ends) so evidence
    # persistence and chunk regeneration cannot interleave, then refuse if evidence exists.
    doc = doc_repo.get_for_update(workspace_id, document_id)
    if not doc:
        raise DocumentNotFoundError("Document not found")
    if EvidenceLinkRepository(session).exists_for_document(document_id):
        raise EvidenceLinksExistError(document_id)

    all_pages = page_repo.list_by_document(document_id)
    pages_with_text = [
        PageText(page_number=p.page_number, text=clean_canonical_text(p.extracted_text))
        for p in all_pages
        if p.extracted_text
        and p.extraction_status == "COMPLETED"
        and len(clean_canonical_text(p.extracted_text)) >= 20
    ]

    # Atomically clear previous chunks for this document
    chunk_repo.delete_by_document(document_id)

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
    any_needs_ocr = any(p.extraction_status == "NEEDS_OCR" for p in all_pages)
    any_failed = any(p.extraction_status == "FAILED" for p in all_pages)

    if any_failed:
        doc.processing_status = DocumentProcessingStatus.FAILED.value
    elif any_needs_ocr:
        doc.processing_status = DocumentProcessingStatus.NEEDS_OCR.value
    else:
        doc.processing_status = DocumentProcessingStatus.READY.value

    session.flush()
    return len(new_chunks)


def regenerate_document_chunks(
    *,
    session: Session,
    settings: Settings,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
) -> int:
    """
    Safely and transactionally regenerate chunks for a document exclusively from canonical page text.
    Re-assesses document processing status (READY, NEEDS_OCR, FAILED).
    Delegates to shared generate_and_persist_canonical_chunks helper.
    Returns new chunk count.
    """
    return generate_and_persist_canonical_chunks(
        session=session,
        settings=settings,
        workspace_id=workspace_id,
        document_id=document_id,
    )


def process_document_ocr(
    *,
    session: Session,
    settings: Settings,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    page_numbers: list[int] | None = None,
    languages: str | None = None,
    dpi: int | None = None,
    provider: str | None = None,
    fallback_to_tesseract: bool | None = None,
) -> tuple[Document, list[PageOCRResult], int, str]:
    """
    Run OCR or Vision transcription on selected pages of an existing document.
    Supports modes:
      - 'tesseract': Local OCR engine
      - 'meta_vision': Meta Muse Spark 1.3 Vision transcription (with optional fallback)
      - 'compare': Runs both Tesseract and Meta Vision, persisting both attempts for inspection
    """
    import pymupdf

    doc_repo = DocumentRepository(session)
    doc = doc_repo.get_by_id(document_id)

    if doc is None or doc.workspace_id != workspace_id:
        raise DocumentNotFoundError(f"Document {document_id} not found in workspace")

    page_repo = DocumentPageRepository(session)
    existing_pages = page_repo.list_by_document(document_id)
    if not existing_pages:
        raise ValidationError("Document has no recorded pages to process")

    page_map = {p.page_number: p for p in existing_pages}

    selected_mode = (provider or settings.scanned_page_extractor).lower()
    if selected_mode not in ("tesseract", "meta_vision", "compare"):
        raise ValidationError(f"Invalid provider mode '{selected_mode}'. Allowed: tesseract, meta_vision, compare")

    # Determine pages
    if page_numbers is not None:
        if not page_numbers:
            raise ValidationError("page_numbers list cannot be empty when provided")
        for num in page_numbers:
            if num not in page_map:
                raise ValidationError(f"Invalid page number {num}; valid range is 1 to {len(existing_pages)}")
        targets = sorted(set(page_numbers))
    else:
        # Default: all pages marked as NEEDS_OCR or FAILED
        targets = [
            p.page_number
            for p in existing_pages
            if p.extraction_status in ("NEEDS_OCR", "FAILED")
            or p.extraction_method == "needs_ocr"
        ]
        if not targets:
            if selected_mode in ("meta_vision", "compare"):
                # In meta_vision or compare mode, default to all pages so user can inspect / compare
                targets = [p.page_number for p in existing_pages]
            else:
                return doc, [], 0, selected_mode

    # Limit check
    max_pages = settings.ocr_max_pages_per_request
    if len(targets) > max_pages:
        raise ValidationError(
            f"Requested {len(targets)} pages for processing, which exceeds maximum limit of {max_pages}"
        )

    # Load stored PDF bytes
    storage = _get_storage(settings)
    try:
        pdf_bytes = storage.load(doc.storage_key)
    except StorageError as exc:
        raise DocumentServiceError(f"Cannot load stored document: {exc}") from exc

    should_fallback = (
        fallback_to_tesseract
        if fallback_to_tesseract is not None
        else settings.meta_vision_fallback_to_tesseract
    )

    selected_langs = languages or settings.ocr_languages
    selected_dpi = dpi or (settings.meta_vision_dpi if selected_mode == "meta_vision" else settings.ocr_dpi)

    # Vision modes spend real model calls (Tesseract is local). Reserve the whole page batch up
    # front so an OCR request is never partially started because only some pages fit the
    # remaining daily budget.
    vision_calls = len(targets) if selected_mode in ("meta_vision", "compare") else 0
    reservation = get_model_budget().reserve(vision_calls) if vision_calls else None

    existing_texts = {p.page_number: p.extracted_text or "" for p in existing_pages}
    ocr_results: list[PageOCRResult] = []
    attempt_repo = PageExtractionAttemptRepository(session)

    if selected_mode == "tesseract":
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
            raise OCRExecutionError(f"Tesseract OCR processing failed: {exc}") from exc

        for res in ocr_results:
            page_obj = page_map[res.page_number]
            attempt_repo.record_attempt(
                document_id=doc.id,
                document_page_id=page_obj.id,
                page_number=res.page_number,
                provider="tesseract",
                extracted_text=res.recognized_text,
                status=res.extraction_status,
                language_configuration=selected_langs,
                usage_metadata=res.metadata,
                error_code=res.error_message,
            )

    else:
        # meta_vision or compare mode
        try:
            pdf_doc = pymupdf.open("pdf", pdf_bytes)
        except Exception as exc:
            if reservation is not None:
                reservation.release_unused()
            raise OCRExecutionError(f"Cannot open PDF: {exc}") from exc

        try:
            meta_extractor = MetaVisionExtractor(settings)

            for page_num in targets:
                page_obj = page_map[page_num]

                if selected_mode == "meta_vision":
                    png_bytes, mime_type = render_page_to_image_bytes(
                        pdf_doc,
                        page_num,
                        dpi=selected_dpi,
                        max_dim=settings.meta_vision_max_image_dim,
                    )
                    if reservation is not None:
                        reservation.consume()
                    vision_res = meta_extractor.transcribe_image_bytes(png_bytes, mime_type=mime_type)

                    if vision_res.status == "COMPLETED" and vision_res.transcription:
                        clean_transcription = clean_canonical_text(vision_res.transcription)
                        combined_text = clean_transcription
                        method = "META_VISION"

                        res = PageOCRResult(
                            page_number=page_num,
                            recognized_text=combined_text,
                            char_count=len(combined_text),
                            extraction_method=method,
                            extraction_status="COMPLETED",
                            languages_used="+".join(vision_res.language_hints) or "auto",
                            provider="meta_vision",
                            metadata={
                                "model": vision_res.model_id,
                                "prompt_version": vision_res.prompt_version,
                                "uncertain_count": len(vision_res.uncertain_regions),
                            },
                            uncertain_regions=vision_res.uncertain_regions,
                            latency_ms=vision_res.latency_ms,
                            model_id=vision_res.model_id,
                            input_hash=vision_res.input_hash,
                        )
                        ocr_results.append(res)

                        attempt_repo.record_attempt(
                            document_id=doc.id,
                            document_page_id=page_obj.id,
                            page_number=page_num,
                            provider="meta_vision",
                            model_id=vision_res.model_id,
                            prompt_version=vision_res.prompt_version,
                            language_configuration="+".join(vision_res.language_hints),
                            extracted_text=clean_transcription,
                            status="COMPLETED",
                            usage_metadata=vision_res.usage_metadata,
                            latency_ms=vision_res.latency_ms,
                            input_hash=vision_res.input_hash,
                        )
                    else:
                        # Vision failed
                        attempt_repo.record_attempt(
                            document_id=doc.id,
                            document_page_id=page_obj.id,
                            page_number=page_num,
                            provider="meta_vision",
                            model_id=vision_res.model_id,
                            prompt_version=vision_res.prompt_version,
                            extracted_text="",
                            status="FAILED",
                            latency_ms=vision_res.latency_ms,
                            input_hash=vision_res.input_hash,
                            error_code=vision_res.error_code or "EmptyOutput",
                        )

                        if should_fallback:
                            logger.warning(
                                "meta_vision_fallback_to_tesseract",
                                extra={"event": "ocr.fallback", "page_number": page_num},
                            )
                            tess_res = run_ocr_on_page(
                                pdf_doc,
                                page_num,
                                languages=selected_langs,
                                dpi=selected_dpi,
                                existing_digital_text="",
                                tessdata_prefix=settings.tessdata_prefix,
                            )
                            tess_res.recognized_text = clean_canonical_text(tess_res.recognized_text)
                            tess_res.metadata["fallback_from"] = "meta_vision"
                            ocr_results.append(tess_res)

                            attempt_repo.record_attempt(
                                document_id=doc.id,
                                document_page_id=page_obj.id,
                                page_number=page_num,
                                provider="tesseract",
                                extracted_text=tess_res.recognized_text,
                                status=tess_res.extraction_status,
                                language_configuration=selected_langs,
                                usage_metadata=tess_res.metadata,
                                error_code=tess_res.error_message,
                            )
                        else:
                            ocr_results.append(
                                PageOCRResult(
                                    page_number=page_num,
                                    recognized_text="",
                                    char_count=0,
                                    extraction_method="META_VISION",
                                    extraction_status="FAILED",
                                    languages_used="auto",
                                    provider="meta_vision",
                                    error_message=vision_res.error_message or "Meta Vision transcription failed",
                                )
                            )

                elif selected_mode == "compare":
                    # Run BOTH Tesseract and Meta Vision
                    # 1. Tesseract
                    tess_res = run_ocr_on_page(
                        pdf_doc,
                        page_num,
                        languages=selected_langs,
                        dpi=selected_dpi,
                        existing_digital_text="",
                        tessdata_prefix=settings.tessdata_prefix,
                    )
                    tess_res.recognized_text = clean_canonical_text(tess_res.recognized_text)
                    attempt_repo.record_attempt(
                        document_id=doc.id,
                        document_page_id=page_obj.id,
                        page_number=page_num,
                        provider="tesseract",
                        extracted_text=tess_res.recognized_text,
                        status=tess_res.extraction_status,
                        language_configuration=selected_langs,
                        usage_metadata=tess_res.metadata,
                        error_code=tess_res.error_message,
                    )

                    # 2. Meta Vision
                    png_bytes, mime_type = render_page_to_image_bytes(
                        pdf_doc,
                        page_num,
                        dpi=selected_dpi,
                        max_dim=settings.meta_vision_max_image_dim,
                    )
                    if reservation is not None:
                        reservation.consume()
                    vision_res = meta_extractor.transcribe_image_bytes(png_bytes, mime_type=mime_type)

                    vision_text = clean_canonical_text(vision_res.transcription)
                    attempt_repo.record_attempt(
                        document_id=doc.id,
                        document_page_id=page_obj.id,
                        page_number=page_num,
                        provider="meta_vision",
                        model_id=vision_res.model_id,
                        prompt_version=vision_res.prompt_version,
                        language_configuration="+".join(vision_res.language_hints),
                        extracted_text=vision_text,
                        status=vision_res.status,
                        usage_metadata=vision_res.usage_metadata,
                        latency_ms=vision_res.latency_ms,
                        input_hash=vision_res.input_hash,
                        error_code=vision_res.error_code,
                    )

                    # In COMPARE mode: Canonical text is preserved; chunks are NOT modified
                    ocr_results.append(tess_res)

        finally:
            if reservation is not None:
                reservation.release_unused()
            pdf_doc.close()

    # Update database transactionally
    try:
        if selected_mode != "compare":
            for res in ocr_results:
                page = page_map[res.page_number]
                clean_text = clean_canonical_text(res.recognized_text)
                if res.extraction_status == "COMPLETED":
                    page.extracted_text = clean_text
                    page.extraction_method = res.extraction_method
                    page.extraction_status = res.extraction_status
                else:
                    page.extraction_status = res.extraction_status

                meta = dict(page.page_metadata or {})
                meta.update(res.metadata)
                meta["canonical_provider"] = res.provider
                meta["canonical_selected_at"] = datetime.now(UTC).isoformat()
                if res.error_message:
                    meta["ocr_error"] = res.error_message
                page.page_metadata = meta

            session.flush()

            new_chunk_count = regenerate_document_chunks(
                session=session,
                settings=settings,
                workspace_id=workspace_id,
                document_id=document_id,
            )
        else:
            # Compare mode: chunks are preserved untouched; attempts are recorded
            chunk_repo = DocumentChunkRepository(session)
            new_chunk_count = len(chunk_repo.list_by_document(document_id))

        session.flush()
        session.commit()

        logger.info(
            "document_processing_finished",
            extra={
                "event": "document.processing_finished",
                "document_id": str(document_id),
                "mode": selected_mode,
                "pages_processed": len(ocr_results),
            },
        )
        return doc, ocr_results, new_chunk_count, selected_mode

    except Exception:
        session.rollback()
        raise


def select_canonical_extraction(
    *,
    session: Session,
    settings: Settings,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    page_number: int | None = None,
    provider: str | None = None,
    attempt_id: uuid.UUID | None = None,
) -> tuple[Document, list[DocumentPage], int]:
    """
    Explicitly select the authoritative canonical extraction for a single page or all pages of a document.
    Transactionally updates document_pages and regenerates chunks from the updated canonical text.

    Args:
        session: SQLAlchemy DB session.
        settings: Application settings.
        workspace_id: UUID of the workspace.
        document_id: UUID of the document.
        page_number: Optional 1-based page number. If None, applies provider selection across all pages.
        provider: Provider name ('tesseract', 'meta_vision', 'native_digital').
        attempt_id: Optional UUID of a specific PageExtractionAttempt to promote to canonical.

    Returns:
        tuple of (Document, list of updated DocumentPage objects, new chunk count)
    """
    if not provider and not attempt_id:
        raise ValidationError("Either 'provider' or 'attempt_id' must be specified.")

    doc_repo = DocumentRepository(session)
    doc = doc_repo.get_by_workspace_and_id(workspace_id, document_id)
    if not doc:
        raise DocumentNotFoundError("Document not found in workspace")

    page_repo = DocumentPageRepository(session)
    attempt_repo = PageExtractionAttemptRepository(session)
    existing_pages = page_repo.list_by_document(document_id)
    if not existing_pages:
        raise ValidationError("Document has no recorded pages.")

    if page_number is not None:
        target_pages = [p for p in existing_pages if p.page_number == page_number]
        if not target_pages:
            raise ValidationError(f"Page number {page_number} does not exist in document.")
    else:
        target_pages = existing_pages

    specific_attempt = None
    if attempt_id is not None:
        specific_attempt = attempt_repo.get_by_id(attempt_id)
        if not specific_attempt:
            raise ValidationError(f"Extraction attempt '{attempt_id}' not found.")
        if specific_attempt.document_id != document_id:
            raise ValidationError("Attempt does not belong to this document.")
        if specific_attempt.status != "COMPLETED":
            raise ValidationError(f"Cannot select non-completed extraction attempt '{attempt_id}' as canonical.")

        # Target the page corresponding to this attempt
        attempt_page = next((p for p in existing_pages if p.id == specific_attempt.document_page_id), None)
        if not attempt_page:
            raise ValidationError("Page for this attempt not found in document.")
        if page_number is not None and attempt_page.page_number != page_number:
            raise ValidationError(
                f"Attempt belongs to page {attempt_page.page_number}, not requested page {page_number}."
            )
        target_pages = [attempt_page]

    now_iso = datetime.now(UTC).isoformat()
    updated_pages: list[DocumentPage] = []

    try:
        with session.begin_nested():
            for page in target_pages:
                if specific_attempt is not None:
                    clean_text = clean_canonical_text(specific_attempt.extracted_text)
                    method = specific_attempt.provider.upper()
                    prov_name = specific_attempt.provider
                    att_id_str = str(specific_attempt.id)
                else:
                    prov_name = (provider or "").lower()
                    if prov_name == "native_digital":
                        native_text = (page.page_metadata or {}).get("native_digital_text")
                        if not native_text and page.extraction_method in ("native_pdf", "DIGITAL_TEXT") and page.extracted_text:
                            native_text = page.extracted_text
                        if not native_text or not native_text.strip():
                            raise ValidationError(f"Page {page.page_number} has no recorded native digital text.")
                        clean_text = clean_canonical_text(native_text)
                        method = page.extraction_method if page.extraction_method in ("native_pdf", "DIGITAL_TEXT") else "native_pdf"
                        att_id_str = None
                    elif prov_name in ("tesseract", "meta_vision"):
                        latest_att = attempt_repo.get_latest_completed(page.id, prov_name)
                        if not latest_att:
                            raise ValidationError(
                                f"Page {page.page_number} has no completed extraction attempt for provider '{provider}'. "
                                "Run extraction first."
                            )
                        clean_text = clean_canonical_text(latest_att.extracted_text)
                        method = prov_name.upper()
                        att_id_str = str(latest_att.id)
                    else:
                        raise ValidationError(
                            f"Unsupported canonical provider '{provider}'. Allowed: tesseract, meta_vision, native_digital"
                        )

                page.extracted_text = clean_text
                page.extraction_method = method
                page.extraction_status = "COMPLETED"

                meta = dict(page.page_metadata or {})
                meta["canonical_provider"] = prov_name
                if att_id_str:
                    meta["canonical_attempt_id"] = att_id_str
                meta["canonical_selected_at"] = now_iso
                page.page_metadata = meta

                updated_pages.append(page)

            session.flush()

            new_chunk_count = regenerate_document_chunks(
                session=session,
                settings=settings,
                workspace_id=workspace_id,
                document_id=document_id,
            )
        session.commit()
    except Exception:
        raise

    logger.info(
        "canonical_extraction_updated",
        extra={
            "event": "canonical.updated",
            "document_id": str(document_id),
            "pages_count": len(updated_pages),
            "chunks_count": new_chunk_count,
            "provider": provider or (specific_attempt.provider if specific_attempt else None),
        },
    )
    return doc, updated_pages, new_chunk_count


