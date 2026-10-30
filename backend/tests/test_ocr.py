"""
Comprehensive tests for Milestone 3: OCR and Multilingual Document Processing.
Covers:
- Language configuration and validation
- Missing Tesseract & missing language handling
- Scanned PDF OCR execution
- Mixed digital + scanned PDF processing
- Chunks regeneration and idempotency
- Malayalam and English OCR integration tests
- Error handling, page limits, corrupted PDFs, and workspace isolation
"""

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.repositories.document_repo import (
    DocumentChunkRepository,
    DocumentPageRepository,
)
from app.ingestion.ocr import (
    PageOCRResult,
    TesseractLanguageNotFoundError,
    TesseractNotFoundError,
    check_tesseract_installed,
    get_available_languages,
    perform_document_ocr,
    validate_ocr_languages,
)
from app.ingestion.storage import LocalStorageAdapter
from app.models.enums import DocumentProcessingStatus
from app.models.workspace import Workspace
from app.services.document_service import (
    ValidationError,
    process_document_ocr,
    upload_document,
)
from tests.helpers import create_synthetic_pdf

# ── 1. OCR Configuration & Language Validation Unit Tests ───────────────────


def test_tesseract_installed_check():
    assert check_tesseract_installed() is True


def test_get_available_languages():
    langs = get_available_languages()
    assert "eng" in langs


def test_validate_ocr_languages_success():
    req = validate_ocr_languages("eng+mal")
    assert "eng" in req
    assert "mal" in req


def test_validate_ocr_languages_missing_language():
    with pytest.raises(TesseractLanguageNotFoundError, match="Tesseract language data missing"):
        validate_ocr_languages("eng+nonexistent_lang_xyz")


def test_validate_ocr_languages_missing_tesseract(monkeypatch):
    monkeypatch.setattr("app.ingestion.ocr.check_tesseract_installed", lambda: False)
    with pytest.raises(TesseractNotFoundError, match="Tesseract OCR is not installed"):
        validate_ocr_languages("eng")


# ── 2. OCR Execution Unit Tests (with Mock & Real Engine) ───────────────────


def test_perform_document_ocr_mocked(monkeypatch):
    """Deterministic mocked unit test."""
    pdf_bytes = create_synthetic_pdf(["Sample page"])

    def _mock_run_ocr(doc, page_num, **kwargs):
        return PageOCRResult(
            page_number=page_num,
            recognized_text="Mocked recognized OCR text",
            char_count=26,
            extraction_method="OCR",
            extraction_status="COMPLETED",
            languages_used="eng+mal",
            metadata={"mock": True},
        )

    monkeypatch.setattr("app.ingestion.ocr.run_ocr_on_page", _mock_run_ocr)

    results = perform_document_ocr(pdf_bytes, [1], languages="eng")
    assert len(results) == 1
    assert results[0].page_number == 1
    assert results[0].recognized_text == "Mocked recognized OCR text"
    assert results[0].extraction_status == "COMPLETED"


def test_perform_document_ocr_empty_output_fails(monkeypatch):
    """Empty OCR output is treated as failure."""
    pdf_bytes = create_synthetic_pdf(["Sample page"])

    def _mock_run_empty(doc, page_num, **kwargs):
        return PageOCRResult(
            page_number=page_num,
            recognized_text="",
            char_count=0,
            extraction_method="OCR",
            extraction_status="FAILED",
            languages_used="eng",
            error_message="No text recognized by OCR",
        )

    monkeypatch.setattr("app.ingestion.ocr.run_ocr_on_page", _mock_run_empty)

    results = perform_document_ocr(pdf_bytes, [1], languages="eng")
    assert len(results) == 1
    assert results[0].extraction_status == "FAILED"
    assert results[0].error_message == "No text recognized by OCR"


# ── 3. Document Service & Workflow Integration Tests ─────────────────────────


def test_digital_pdf_does_not_unnecessarily_invoke_ocr(
    db_session: Session, test_workspace: Workspace, temp_storage_dir: str
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    # Digital PDF with plenty of text
    pdf_bytes = create_synthetic_pdf([
        "Digital PDF content with sufficient length that does not require optical character recognition."
    ])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="digital.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    assert doc.processing_status == DocumentProcessingStatus.READY.value

    # Triggering default OCR on this doc finds 0 pages needing OCR
    doc, results, regenerated_count, mode = process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
    )
    assert len(results) == 0
    assert regenerated_count == 0
    assert mode == "tesseract"


def test_scanned_pdf_is_ocr_processed_and_chunks_regenerated(
    db_session: Session, test_workspace: Workspace, temp_storage_dir: str, monkeypatch
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    # Scanned page (less than min_text_density_chars=30)
    pdf_bytes = create_synthetic_pdf(["img"])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="scanned.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )
    assert doc.processing_status == DocumentProcessingStatus.NEEDS_OCR.value

    # Mock OCR execution for this test
    def _mock_ocr(pdf, targets, **kwargs):
        return [
            PageOCRResult(
                page_number=1,
                recognized_text="Government notice regarding operational safety guidelines and regulations.",
                char_count=74,
                extraction_method="OCR",
                extraction_status="COMPLETED",
                languages_used="eng+mal",
            )
        ]

    monkeypatch.setattr("app.services.document_service.perform_document_ocr", _mock_ocr)

    updated_doc, results, new_chunks_count, _ = process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
    )

    assert updated_doc.processing_status == DocumentProcessingStatus.READY.value
    assert len(results) == 1
    assert results[0].page_number == 1
    assert new_chunks_count >= 1

    # Verify pages and chunks in DB
    page_repo = DocumentPageRepository(db_session)
    pages = page_repo.list_by_document(doc.id)
    assert pages[0].extraction_method == "OCR"
    assert pages[0].extraction_status == "COMPLETED"
    assert "safety guidelines" in pages[0].extracted_text

    chunk_repo = DocumentChunkRepository(db_session)
    chunks = chunk_repo.list_by_document(doc.id)
    assert len(chunks) == new_chunks_count
    assert chunks[0].page_start == 1


def test_multi_page_mixed_pdf_preserves_digital_text(
    db_session: Session, test_workspace: Workspace, temp_storage_dir: str, monkeypatch
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    digital_page_text = "Page 1: Digital text that is clear, well-formed, and perfectly extractable."
    pdf_bytes = create_synthetic_pdf([
        digital_page_text,
        "scanned",  # Page 2 needs OCR
    ])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="mixed.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )
    assert doc.processing_status == DocumentProcessingStatus.NEEDS_OCR.value

    def _mock_ocr(pdf, targets, **kwargs):
        assert targets == [2]  # Only page 2 should be targeted by default
        return [
            PageOCRResult(
                page_number=2,
                recognized_text="Page 2: OCR extracted text from scanned annexure table.",
                char_count=55,
                extraction_method="OCR",
                extraction_status="COMPLETED",
                languages_used="eng+mal",
            )
        ]

    monkeypatch.setattr("app.services.document_service.perform_document_ocr", _mock_ocr)

    updated_doc, results, chunks_count, _ = process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
    )

    assert updated_doc.processing_status == DocumentProcessingStatus.READY.value

    page_repo = DocumentPageRepository(db_session)
    p1 = page_repo.get_by_page_number(doc.id, 1)
    p2 = page_repo.get_by_page_number(doc.id, 2)

    # Original digital text on page 1 is preserved
    assert p1.extracted_text == digital_page_text
    assert p1.extraction_method == "native_pdf"

    # Page 2 now has OCR text
    assert "annexure table" in p2.extracted_text
    assert p2.extraction_method == "OCR"


def test_repeated_ocr_does_not_duplicate_chunks(
    db_session: Session, test_workspace: Workspace, temp_storage_dir: str, monkeypatch
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    pdf_bytes = create_synthetic_pdf(["img"])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="repeat.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    def _mock_ocr(pdf, targets, **kwargs):
        return [
            PageOCRResult(
                page_number=1,
                recognized_text="Recognized text for repeat check.",
                char_count=33,
                extraction_method="OCR",
                extraction_status="COMPLETED",
                languages_used="eng",
            )
        ]

    monkeypatch.setattr("app.services.document_service.perform_document_ocr", _mock_ocr)

    # First OCR
    process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        page_numbers=[1],
    )
    chunk_repo = DocumentChunkRepository(db_session)
    first_count = len(chunk_repo.list_by_document(doc.id))

    # Second OCR on same page
    process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        page_numbers=[1],
    )
    second_count = len(chunk_repo.list_by_document(doc.id))

    assert first_count == second_count


def test_ocr_invalid_page_number_rejected(
    db_session: Session, test_workspace: Workspace, temp_storage_dir: str
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    pdf_bytes = create_synthetic_pdf(["Single page"])
    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="single.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    with pytest.raises(ValidationError, match="Invalid page number"):
        process_document_ocr(
            session=db_session,
            settings=settings,
            workspace_id=test_workspace.id,
            document_id=doc.id,
            page_numbers=[999],
        )


def test_ocr_exceeding_page_limit_rejected(
    db_session: Session, test_workspace: Workspace, temp_storage_dir: str
):
    settings = get_settings().model_copy(
        update={"document_storage_dir": temp_storage_dir, "ocr_max_pages_per_request": 1}
    )
    pdf_bytes = create_synthetic_pdf(["p1", "p2"])
    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="two_page.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    with pytest.raises(ValidationError, match="exceeds maximum limit"):
        process_document_ocr(
            session=db_session,
            settings=settings,
            workspace_id=test_workspace.id,
            document_id=doc.id,
            page_numbers=[1, 2],
        )


# ── 4. API Integration Tests ──────────────────────────────────────────────────


def test_api_trigger_ocr_success(
    client: TestClient, test_workspace: Workspace, temp_storage_dir: str, monkeypatch
):
    monkeypatch.setattr(
        "app.services.document_service._get_storage",
        lambda settings: LocalStorageAdapter(temp_storage_dir),
    )

    pdf_bytes = create_synthetic_pdf(["scanned_doc"])

    # Upload
    upload_res = client.post(
        f"/api/v1/workspaces/{test_workspace.id}/documents",
        files={"file": ("scanned_policy.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    doc_id = upload_res.json()["id"]

    def _mock_ocr(pdf, targets, **kwargs):
        return [
            PageOCRResult(
                page_number=1,
                recognized_text="Full OCR text extracted via API endpoint.",
                char_count=42,
                extraction_method="OCR",
                extraction_status="COMPLETED",
                languages_used="eng+mal",
            )
        ]

    monkeypatch.setattr("app.services.document_service.perform_document_ocr", _mock_ocr)

    # Call OCR endpoint
    ocr_res = client.post(
        f"/api/v1/workspaces/{test_workspace.id}/documents/{doc_id}/ocr",
        json={"languages": "eng+mal", "dpi": 300},
    )
    assert ocr_res.status_code == 200
    data = ocr_res.json()
    assert data["document_id"] == doc_id
    assert data["processing_status"] == "READY"
    assert data["pages_processed"] == 1
    assert data["chunks_regenerated"] >= 1
    assert data["results"][0]["page_number"] == 1
    assert data["results"][0]["extraction_status"] == "COMPLETED"


def test_api_trigger_ocr_document_not_found(client: TestClient, test_workspace: Workspace):
    fake_doc_id = uuid.uuid4()
    res = client.post(
        f"/api/v1/workspaces/{test_workspace.id}/documents/{fake_doc_id}/ocr",
        json={},
    )
    assert res.status_code == 404


def test_api_trigger_ocr_workspace_isolation(
    client: TestClient,
    db_session: Session,
    test_workspace: Workspace,
    temp_storage_dir: str,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.document_service._get_storage",
        lambda settings: LocalStorageAdapter(temp_storage_dir),
    )

    pdf_bytes = create_synthetic_pdf(["scanned"])
    res = client.post(
        f"/api/v1/workspaces/{test_workspace.id}/documents",
        files={"file": ("doc.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    doc_id = res.json()["id"]

    other_ws = Workspace(name="Other WS", slug=f"other-ws-{uuid.uuid4().hex[:6]}")
    db_session.add(other_ws)
    db_session.flush()

    # Accessing with wrong workspace returns 404
    ocr_res = client.post(
        f"/api/v1/workspaces/{other_ws.id}/documents/{doc_id}/ocr",
        json={},
    )
    assert ocr_res.status_code == 404


# ── 5. Actual Real-Engine Integration Tests ────────────────────────────────────


@pytest.mark.skipif(not check_tesseract_installed(), reason="Tesseract is not installed")
def test_real_engine_ocr_english_integration():
    """Live OCR integration test with real PyMuPDF + Tesseract on English text."""
    test_text = "PRAMAAN AI VERIFICATION PROTOCOL 2026"
    pdf_bytes = create_synthetic_pdf([test_text])

    results = perform_document_ocr(pdf_bytes, [1], languages="eng", dpi=200)
    assert len(results) == 1
    assert results[0].extraction_status == "COMPLETED"
    assert "PRAMAAN" in results[0].recognized_text or "VERIFICATION" in results[0].recognized_text


@pytest.mark.skipif(
    "mal" not in get_available_languages(), reason="Malayalam (mal) traineddata not installed"
)
def test_real_engine_ocr_malayalam_integration():
    """Live OCR integration test with real PyMuPDF + Tesseract on Malayalam language configuration."""
    test_content = "KERALA GOVT CIRCULAR: പ്രമാണ നിർദ്ദേശം"
    pdf_bytes = create_synthetic_pdf([test_content])
    results = perform_document_ocr(pdf_bytes, [1], languages="eng+mal", dpi=300)
    assert len(results) == 1
    assert results[0].extraction_status == "COMPLETED"
    assert "KERALA" in results[0].recognized_text or "CIRCULAR" in results[0].recognized_text
