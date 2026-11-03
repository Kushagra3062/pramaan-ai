"""
Tests for Milestone 2: Document Intake, PDF Storage, Text Extraction and Chunking.
"""

import io
import shutil
import tempfile
import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.engine import get_db
from app.db.repositories.document_repo import (
    DocumentChunkRepository,
    DocumentPageRepository,
)
from app.ingestion.chunking import PageText, chunk_document
from app.ingestion.extraction import (
    PDFExtractionError,
    extract_pdf,
    validate_pdf_signature,
)
from app.ingestion.storage import LocalStorageAdapter, StorageError
from app.main import app
from app.models.enums import DocumentProcessingStatus
from app.models.workspace import Workspace
from app.services.document_service import (
    DuplicateDocumentError,
    ValidationError,
    WorkspaceNotFoundError,
    upload_document,
)
from tests.helpers import create_synthetic_pdf


@pytest.fixture
def temp_storage_dir() -> Generator[str, None, None]:
    """Temporary storage directory cleaned up after each test."""
    temp_dir = tempfile.mkdtemp(prefix="pramaan_test_storage_")
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """TestClient that overrides get_db dependency with test transactional session."""
    def _override_get_db():
        yield db_session

    from app.api.auth import require_workspace_read, require_workspace_write

    app.dependency_overrides[get_db] = _override_get_db
    # Authorization is covered by tests/test_workspace_authorization.py.
    app.dependency_overrides[require_workspace_read] = lambda: None
    app.dependency_overrides[require_workspace_write] = lambda: None
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── 1. Storage adapter tests ──────────────────────────────────────────────────


def test_storage_adapter_save_load_delete(temp_storage_dir: str):
    adapter = LocalStorageAdapter(temp_storage_dir)
    ws_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    content = b"Mock PDF binary data"

    # Save
    key = adapter.save(ws_id, doc_id, content, "pdf")
    assert key == f"{ws_id}/{doc_id}.pdf"

    # Load
    loaded = adapter.load(key)
    assert loaded == content

    # Delete
    adapter.delete(key)
    with pytest.raises(StorageError):
        adapter.load(key)


def test_storage_adapter_path_traversal(temp_storage_dir: str):
    adapter = LocalStorageAdapter(temp_storage_dir)
    with pytest.raises(StorageError, match="Path traversal"):
        adapter.load("../../etc/passwd")


# ── 2. PDF extraction unit tests ──────────────────────────────────────────────


def test_validate_pdf_signature():
    assert validate_pdf_signature(b"%PDF-1.4\n...") is True
    assert validate_pdf_signature(b"NOT_A_PDF") is False


def test_pdf_extraction_single_page():
    sample_text = "This is a standard single page operational compliance policy document."
    pdf_bytes = create_synthetic_pdf([sample_text])

    result = extract_pdf(pdf_bytes)
    assert result.page_count == 1
    assert result.overall_status == "COMPLETED"
    assert len(result.pages) == 1
    assert result.pages[0].page_number == 1
    assert "operational compliance policy" in result.pages[0].text
    assert result.pages[0].extraction_status == "COMPLETED"
    assert len(result.needs_ocr_pages) == 0


def test_pdf_extraction_multi_page():
    p1 = "First page: General terms of operation and regulatory requirements."
    p2 = "Second page: Financial disclosures, timeline constraints, and escalation procedures."
    pdf_bytes = create_synthetic_pdf([p1, p2])

    result = extract_pdf(pdf_bytes)
    assert result.page_count == 2
    assert result.overall_status == "COMPLETED"
    assert result.pages[0].page_number == 1
    assert result.pages[1].page_number == 2
    assert "General terms" in result.pages[0].text
    assert "Financial disclosures" in result.pages[1].text


def test_pdf_extraction_empty_or_scanned_detection():
    # Page with zero or < 30 chars
    scanned_page = "Scanned"  # only 7 chars, below threshold 30
    pdf_bytes = create_synthetic_pdf([scanned_page])

    result = extract_pdf(pdf_bytes, min_text_chars=30)
    assert result.page_count == 1
    assert result.overall_status == "NEEDS_OCR"
    assert result.needs_ocr_pages == [1]
    assert result.pages[0].extraction_status == "NEEDS_OCR"
    assert result.pages[0].extraction_method == "needs_ocr"


def test_pdf_extraction_corrupted_pdf():
    corrupted_data = b"%PDF-corrupted-bytes-that-cannot-be-parsed"
    with pytest.raises(PDFExtractionError):
        extract_pdf(corrupted_data)


# ── 3. Chunking unit tests ────────────────────────────────────────────────────


def test_chunking_deterministic_and_ordering():
    pages = [
        PageText(page_number=1, text="Page 1: " + "alpha " * 40),
        PageText(page_number=2, text="Page 2: " + "beta " * 40),
    ]

    chunks = chunk_document(pages, chunk_size=150, chunk_overlap=30)
    assert len(chunks) > 1
    for i, chunk in enumerate(chunks):
        assert chunk.chunk_index == i
        assert chunk.page_start in (1, 2)
        assert chunk.page_end in (1, 2)
        assert chunk.page_start <= chunk.page_end
        assert len(chunk.content) > 0
        assert len(chunk.content_hash) == 64


def test_chunking_empty_or_minimal():
    chunks = chunk_document([])
    assert chunks == []

    chunks_short = chunk_document([PageText(page_number=1, text="tiny")])
    assert chunks_short == []


# ── 4. Document service & transaction tests ───────────────────────────────────


def test_service_upload_valid_single_page(
    db_session: Session, test_workspace: Workspace, temp_storage_dir: str
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    text_content = "This document establishes standard operating procedure for audit tracking."
    pdf_bytes = create_synthetic_pdf([text_content])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="compliance_policy.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    assert doc.id is not None
    assert doc.processing_status == DocumentProcessingStatus.READY.value
    assert doc.page_count == 1
    assert doc.original_filename == "compliance_policy.pdf"

    # Check pages in DB
    page_repo = DocumentPageRepository(db_session)
    pages = page_repo.list_by_document(doc.id)
    assert len(pages) == 1
    assert pages[0].page_number == 1
    assert "operating procedure" in pages[0].extracted_text

    # Check chunks in DB
    chunk_repo = DocumentChunkRepository(db_session)
    chunks = chunk_repo.list_by_document(doc.id)
    assert len(chunks) >= 1
    assert chunks[0].page_start == 1


def test_service_upload_duplicate_detection(
    db_session: Session, test_workspace: Workspace, temp_storage_dir: str
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    pdf_bytes = create_synthetic_pdf(["Duplicate test text for hash checking."])

    doc1 = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="doc1.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )
    assert doc1 is not None

    with pytest.raises(DuplicateDocumentError) as exc_info:
        upload_document(
            session=db_session,
            settings=settings,
            workspace_id=test_workspace.id,
            filename="doc2.pdf",
            content_type="application/pdf",
            file_data=pdf_bytes,
        )
    assert exc_info.value.existing_document.id == doc1.id


def test_service_upload_scanned_pdf_needs_ocr(
    db_session: Session, test_workspace: Workspace, temp_storage_dir: str
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    pdf_bytes = create_synthetic_pdf(["img"])  # < 30 chars

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="scanned.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )
    assert doc.processing_status == DocumentProcessingStatus.NEEDS_OCR.value


def test_service_upload_validation_errors(
    db_session: Session, test_workspace: Workspace, temp_storage_dir: str
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})

    # Invalid extension
    with pytest.raises(ValidationError, match="extension not allowed"):
        upload_document(
            session=db_session,
            settings=settings,
            workspace_id=test_workspace.id,
            filename="doc.docx",
            content_type="application/pdf",
            file_data=b"%PDF-1.4",
        )

    # Invalid mime type
    with pytest.raises(ValidationError, match="Content type"):
        upload_document(
            session=db_session,
            settings=settings,
            workspace_id=test_workspace.id,
            filename="doc.pdf",
            content_type="image/jpeg",
            file_data=b"%PDF-1.4",
        )

    # Invalid magic signature
    with pytest.raises(ValidationError, match="valid PDF signature"):
        upload_document(
            session=db_session,
            settings=settings,
            workspace_id=test_workspace.id,
            filename="doc.pdf",
            content_type="application/pdf",
            file_data=b"NOT_A_PDF_STREAM",
        )

    # Oversized file
    with pytest.raises(ValidationError, match="exceeds maximum"):
        upload_document(
            session=db_session,
            settings=settings.model_copy(update={"max_upload_mb": 1}),
            workspace_id=test_workspace.id,
            filename="doc.pdf",
            content_type="application/pdf",
            file_data=b"%PDF-" + b"0" * (2 * 1024 * 1024),
        )


def test_service_upload_nonexistent_workspace(db_session: Session, temp_storage_dir: str):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    with pytest.raises(WorkspaceNotFoundError):
        upload_document(
            session=db_session,
            settings=settings,
            workspace_id=uuid.uuid4(),
            filename="doc.pdf",
            content_type="application/pdf",
            file_data=b"%PDF-1.4 test",
        )


# ── 5. API endpoint integration tests ─────────────────────────────────────────


def test_api_upload_and_retrieve_flow(
    client: TestClient, test_workspace: Workspace, temp_storage_dir: str, monkeypatch
):
    monkeypatch.setattr(
        "app.services.document_service._get_storage",
        lambda settings: LocalStorageAdapter(temp_storage_dir),
    )

    pdf_bytes = create_synthetic_pdf([
        "Page 1: Corporate governance requirement on multi-tiered oversight.",
        "Page 2: Implementation guidance and operational conflict resolution procedures.",
    ])

    # 1. Upload
    response = client.post(
        f"/api/v1/workspaces/{test_workspace.id}/documents",
        files={"file": ("governance_policy.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert response.status_code == 201
    upload_data = response.json()
    assert "id" in upload_data
    assert upload_data["processing_status"] in ("READY", "COMPLETED")
    doc_id = upload_data["id"]

    # 2. Get document metadata
    response = client.get(f"/api/v1/workspaces/{test_workspace.id}/documents/{doc_id}")
    assert response.status_code == 200
    doc_data = response.json()
    assert doc_data["id"] == doc_id
    assert doc_data["original_filename"] == "governance_policy.pdf"
    assert doc_data["page_count"] == 2
    assert doc_data["file_size_bytes"] == len(pdf_bytes)

    # 3. Get pages
    response = client.get(f"/api/v1/workspaces/{test_workspace.id}/documents/{doc_id}/pages")
    assert response.status_code == 200
    pages_data = response.json()
    assert pages_data["total"] == 2
    assert len(pages_data["items"]) == 2
    assert pages_data["items"][0]["page_number"] == 1
    assert "Corporate governance" in pages_data["items"][0]["extracted_text"]
    assert pages_data["items"][1]["page_number"] == 2
    assert "Implementation guidance" in pages_data["items"][1]["extracted_text"]

    # 4. Get chunks
    response = client.get(f"/api/v1/workspaces/{test_workspace.id}/documents/{doc_id}/chunks")
    assert response.status_code == 200
    chunks_data = response.json()
    assert chunks_data["total"] >= 1
    assert chunks_data["items"][0]["document_id"] == doc_id
    assert chunks_data["items"][0]["workspace_id"] == str(test_workspace.id)

    # 5. List documents
    response = client.get(f"/api/v1/workspaces/{test_workspace.id}/documents")
    assert response.status_code == 200
    list_data = response.json()
    assert list_data["total"] >= 1
    assert any(d["id"] == doc_id for d in list_data["items"])


def test_api_upload_duplicate_returns_existing(
    client: TestClient, test_workspace: Workspace, temp_storage_dir: str, monkeypatch
):
    monkeypatch.setattr(
        "app.services.document_service._get_storage",
        lambda settings: LocalStorageAdapter(temp_storage_dir),
    )

    pdf_bytes = create_synthetic_pdf(["Duplicate API test payload content."])

    # First upload
    res1 = client.post(
        f"/api/v1/workspaces/{test_workspace.id}/documents",
        files={"file": ("doc_a.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert res1.status_code == 201
    doc_id = res1.json()["id"]

    # Second upload of identical file
    res2 = client.post(
        f"/api/v1/workspaces/{test_workspace.id}/documents",
        files={"file": ("doc_b.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert res2.status_code == 201
    res2_data = res2.json()
    assert "existing_document_id" in res2_data
    assert res2_data["existing_document_id"] == doc_id


def test_api_workspace_isolation(
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

    pdf_bytes = create_synthetic_pdf(["Workspace isolation test content."])
    res = client.post(
        f"/api/v1/workspaces/{test_workspace.id}/documents",
        files={"file": ("isolated.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert res.status_code == 201
    doc_id = res.json()["id"]

    # Second workspace
    other_ws = Workspace(name="Other WS", slug=f"other-ws-{uuid.uuid4().hex[:6]}")
    db_session.add(other_ws)
    db_session.flush()

    # Querying the document with other workspace ID must 404
    get_res = client.get(f"/api/v1/workspaces/{other_ws.id}/documents/{doc_id}")
    assert get_res.status_code == 404

    # Pages endpoint with other workspace ID must 404
    pages_res = client.get(f"/api/v1/workspaces/{other_ws.id}/documents/{doc_id}/pages")
    assert pages_res.status_code == 404

    # Chunks endpoint with other workspace ID must 404
    chunks_res = client.get(f"/api/v1/workspaces/{other_ws.id}/documents/{doc_id}/chunks")
    assert chunks_res.status_code == 404
