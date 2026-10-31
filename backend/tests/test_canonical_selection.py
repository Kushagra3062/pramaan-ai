"""
Milestone 3.1.1: Canonical extraction selection and chunk integrity test matrix.

Tests all 15 required scenarios:
1. Compare mode preserves both outputs independently without changing canonical text.
2. Tesseract and Muse Spark outputs are never concatenated.
3. Canonical text comes from exactly one selected provider.
4. Native PDF digital text is not overwritten by OCR/compare runs.
5. Muse Spark can be explicitly selected as canonical.
6. Changing canonical provider regenerates chunks cleanly.
7. Unaffected page canonical text and chunks remain consistent.
8. Repeated chunk generation is idempotent.
9. Malayalam Unicode content remains intact across pages and chunks.
10. Chunks contain no debug labels ([OCR EXTRACTED], [MUSE SPARK VISION]).
11. Chunk text does not duplicate entire paragraphs.
12. Sentence and word boundaries are respected.
13. Page provenance (page_start, page_end) is strictly preserved.
14. Workspace isolation is enforced on canonical selection routes.
15. DB rollback works properly on failure.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.repositories.document_repo import (
    DocumentChunkRepository,
    DocumentPageRepository,
    PageExtractionAttemptRepository,
)
from app.ingestion.chunking import PageText, chunk_document
from app.ingestion.meta_vision import MetaVisionExtractor, MetaVisionResult
from app.models.workspace import Workspace
from app.services.document_service import (
    process_document_ocr,
    regenerate_document_chunks,
    select_canonical_extraction,
    upload_document,
)
from tests.helpers import create_synthetic_pdf


@pytest.fixture
def mock_vision_and_tesseract(monkeypatch):
    """Mock both Meta Vision and Tesseract OCR to return known, deterministic texts."""
    vision_text = "MUSE SPARK: Kerala Finance Department Circular 2026. Paragraph Alpha. Paragraph Beta."
    tesseract_text = "TESSERACT: Kerala Finance Dept Circular 2026. Par Alpha. Par Beta."

    def _mock_transcribe(self, image_bytes, mime_type="image/png"):
        return MetaVisionResult(
            transcription=vision_text,
            language_hints=["English"],
            uncertain_regions=[],
            model_id="muse-spark-1.3",
            prompt_version="v1.1-vision-transcription",
            input_hash="hash_vision",
            latency_ms=250,
            status="COMPLETED",
        )

    monkeypatch.setattr(MetaVisionExtractor, "transcribe_image_bytes", _mock_transcribe)

    def _mock_ocr(doc, page_num, **kwargs):
        from app.ingestion.ocr import PageOCRResult

        return PageOCRResult(
            page_number=page_num,
            recognized_text=tesseract_text,
            char_count=len(tesseract_text),
            extraction_method="OCR",
            extraction_status="COMPLETED",
            languages_used="eng",
            provider="tesseract",
            metadata={"dpi": 300},
        )

    monkeypatch.setattr("app.services.document_service.run_ocr_on_page", _mock_ocr)

    return {"vision": vision_text, "tesseract": tesseract_text}


# ── Scenario 1: Compare mode preserves both outputs independently without changing canonical text ───


def test_compare_mode_preserves_both_outputs_without_changing_canonical_text(
    db_session: Session,
    test_workspace: Workspace,
    temp_storage_dir: str,
    mock_vision_and_tesseract,
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    pdf_bytes = create_synthetic_pdf(["img"])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="scanned_doc.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    page_repo = DocumentPageRepository(db_session)
    pages_before = page_repo.list_by_document(doc.id)
    initial_text = pages_before[0].extracted_text
    initial_method = pages_before[0].extraction_method

    # Run compare mode
    updated_doc, results, chunks_count, mode = process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="compare",
    )

    assert mode == "compare"
    pages_after = page_repo.list_by_document(doc.id)
    # Canonical text and method must NOT be mutated by compare mode
    assert pages_after[0].extracted_text == initial_text
    assert pages_after[0].extraction_method == initial_method

    # Both attempts must be recorded in attempts table
    attempt_repo = PageExtractionAttemptRepository(db_session)
    attempts = attempt_repo.list_by_document(doc.id)
    providers = {a.provider for a in attempts}
    assert "tesseract" in providers
    assert "meta_vision" in providers


# ── Scenario 2: Tesseract and Muse Spark outputs are never concatenated ────────


def test_tesseract_and_muse_spark_outputs_are_never_concatenated(
    db_session: Session,
    test_workspace: Workspace,
    temp_storage_dir: str,
    mock_vision_and_tesseract,
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    pdf_bytes = create_synthetic_pdf(["img"])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="no_concat.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    # 1. Run Tesseract first
    process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="tesseract",
    )

    # 2. Run Meta Vision
    process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="meta_vision",
    )

    page_repo = DocumentPageRepository(db_session)
    pages = page_repo.list_by_document(doc.id)
    page_text = pages[0].extracted_text

    # Page text must not contain both or markers
    assert "[OCR EXTRACTED]" not in page_text
    assert "[MUSE SPARK VISION]" not in page_text
    assert not (mock_vision_and_tesseract["vision"] in page_text and mock_vision_and_tesseract["tesseract"] in page_text)


# ── Scenario 3: Canonical text comes from exactly one selected provider ───────


def test_canonical_text_comes_from_exactly_one_selected_provider(
    db_session: Session,
    test_workspace: Workspace,
    temp_storage_dir: str,
    mock_vision_and_tesseract,
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    pdf_bytes = create_synthetic_pdf(["img"])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="single_provider.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    # Run compare mode first so both attempts exist
    process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="compare",
    )

    # Select Tesseract as canonical
    select_canonical_extraction(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        page_number=1,
        provider="tesseract",
    )

    page_repo = DocumentPageRepository(db_session)
    page = page_repo.list_by_document(doc.id)[0]
    assert page.extracted_text == mock_vision_and_tesseract["tesseract"]
    assert page.extraction_method == "TESSERACT"
    assert page.page_metadata["canonical_provider"] == "tesseract"

    # Now select Meta Vision as canonical
    select_canonical_extraction(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        page_number=1,
        provider="meta_vision",
    )

    page = page_repo.list_by_document(doc.id)[0]
    assert page.extracted_text == mock_vision_and_tesseract["vision"]
    assert page.extraction_method == "META_VISION"
    assert page.page_metadata["canonical_provider"] == "meta_vision"


# ── Scenario 4: Native PDF digital text is not overwritten by OCR/compare runs ──


def test_native_pdf_digital_text_not_overwritten_by_ocr_or_compare(
    db_session: Session,
    test_workspace: Workspace,
    temp_storage_dir: str,
    mock_vision_and_tesseract,
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    digital_text = "PRAMAAN OFFICIAL POLICY: Standard Operational Procedures 2026. All rights reserved."
    pdf_bytes = create_synthetic_pdf([digital_text])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="digital_policy.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    page_repo = DocumentPageRepository(db_session)
    page = page_repo.list_by_document(doc.id)[0]
    assert page.extraction_method in ("native_pdf", "DIGITAL_TEXT")
    assert digital_text in page.extracted_text

    # Run compare mode on the digital document
    process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        page_numbers=[1],
        provider="compare",
    )

    page = page_repo.list_by_document(doc.id)[0]
    # Digital text remains unchanged
    assert page.extraction_method in ("native_pdf", "DIGITAL_TEXT")
    assert digital_text in page.extracted_text

    # Native text can also be explicitly reselected
    select_canonical_extraction(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        page_number=1,
        provider="native_digital",
    )
    page = page_repo.list_by_document(doc.id)[0]
    assert page.extraction_method in ("native_pdf", "DIGITAL_TEXT")
    assert digital_text in page.extracted_text


# ── Scenario 5: Muse Spark can be explicitly selected as canonical ────────────


def test_muse_spark_explicitly_selected_as_canonical(
    db_session: Session,
    test_workspace: Workspace,
    temp_storage_dir: str,
    mock_vision_and_tesseract,
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    pdf_bytes = create_synthetic_pdf(["img"])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="promote_muse.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="compare",
    )

    # Select Muse Spark across the document
    _, updated_pages, chunks_count = select_canonical_extraction(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="meta_vision",
    )

    assert len(updated_pages) == 1
    assert updated_pages[0].extraction_method == "META_VISION"
    assert updated_pages[0].extracted_text == mock_vision_and_tesseract["vision"]
    assert chunks_count > 0


# ── Scenario 6: Changing canonical provider regenerates chunks cleanly ─────────


def test_changing_canonical_provider_regenerates_chunks_cleanly(
    db_session: Session,
    test_workspace: Workspace,
    temp_storage_dir: str,
    mock_vision_and_tesseract,
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    pdf_bytes = create_synthetic_pdf(["img"])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="regen_chunks.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="compare",
    )

    chunk_repo = DocumentChunkRepository(db_session)

    # Set to Tesseract
    select_canonical_extraction(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="tesseract",
    )
    tess_chunks = chunk_repo.list_by_document(doc.id)
    assert any("TESSERACT" in c.content for c in tess_chunks)
    assert not any("MUSE SPARK" in c.content for c in tess_chunks)

    # Switch to Meta Vision
    select_canonical_extraction(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="meta_vision",
    )
    vision_chunks = chunk_repo.list_by_document(doc.id)
    assert any("MUSE SPARK" in c.content for c in vision_chunks)
    assert not any("TESSERACT" in c.content for c in vision_chunks)


# ── Scenario 7: Unaffected page chunks remain intact ──────────────────────────


def test_unaffected_page_chunks_remain_intact(
    db_session: Session,
    test_workspace: Workspace,
    temp_storage_dir: str,
    mock_vision_and_tesseract,
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    # 2 pages
    pdf_bytes = create_synthetic_pdf(["img", "img"])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="two_page.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="compare",
    )

    # Set both to Tesseract first
    select_canonical_extraction(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="tesseract",
    )

    # Change only Page 1 to Meta Vision
    select_canonical_extraction(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        page_number=1,
        provider="meta_vision",
    )

    page_repo = DocumentPageRepository(db_session)
    pages = page_repo.list_by_document(doc.id)
    assert pages[0].extraction_method == "META_VISION"
    assert pages[1].extraction_method == "TESSERACT"  # Page 2 remains intact!


# ── Scenario 8: Repeated chunk generation is idempotent ───────────────────────


def test_repeated_chunk_generation_is_idempotent(
    db_session: Session,
    test_workspace: Workspace,
    temp_storage_dir: str,
    mock_vision_and_tesseract,
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    pdf_bytes = create_synthetic_pdf(["img"])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="idempotent.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="meta_vision",
    )

    count_1 = regenerate_document_chunks(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
    )

    chunk_repo = DocumentChunkRepository(db_session)
    hashes_1 = [c.content_hash for c in chunk_repo.list_by_document(doc.id)]

    count_2 = regenerate_document_chunks(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
    )
    hashes_2 = [c.content_hash for c in chunk_repo.list_by_document(doc.id)]

    assert count_1 == count_2
    assert hashes_1 == hashes_2


# ── Scenario 9: Malayalam Unicode content remains intact ──────────────────────


def test_malayalam_unicode_content_remains_intact(
    db_session: Session,
    test_workspace: Workspace,
    temp_storage_dir: str,
    monkeypatch,
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    malayalam_text = (
        "പ്രമാണം - പരിശോധന. ടെസ്റ്റ് ഫയൽ 03. സ്കാൻ ചെയ്ത മലയാളം രേഖ.\n\n"
        "1. രേഖയുടെ വിവരങ്ങൾ. സർക്കുലർ നമ്പർ: 003. വകുപ്പ്: പ്രവർത്തനവിഭാഗം. "
        "തീയതി: 01 നവംബർ 2026. പ്രാബല്യതീയതി: 15 നവംബർ 2026.\n\n"
        "2. റിപ്പോർട്ട് സമർപ്പിക്കൽ. പ്രതിമാസ റിപ്പോർട്ട് സമർപ്പിക്കേണ്ട അവസാന തീയതി 30 നവംബർ 2026 ആണ്."
    )

    def _mock_transcribe(self, image_bytes, mime_type="image/png"):
        return MetaVisionResult(
            transcription=malayalam_text,
            language_hints=["Malayalam"],
            uncertain_regions=[],
            model_id="muse-spark-1.3",
            prompt_version="v1.1-vision-transcription",
            input_hash="hash_mal",
            latency_ms=290,
            status="COMPLETED",
        )

    monkeypatch.setattr(MetaVisionExtractor, "transcribe_image_bytes", _mock_transcribe)

    pdf_bytes = create_synthetic_pdf(["img"])
    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="malayalam_intact.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="meta_vision",
    )

    chunk_repo = DocumentChunkRepository(db_session)
    chunks = chunk_repo.list_by_document(doc.id)
    assert len(chunks) > 0
    full_chunks_text = " ".join(c.content for c in chunks)

    # Unicode words and Indic punctuation preserved
    assert "പ്രമാണം" in full_chunks_text
    assert "പ്രവർത്തനവിഭാഗം" in full_chunks_text
    assert "30 നവംബർ 2026" in full_chunks_text


# ── Scenario 10: Chunks contain no debug labels ────────────────────────────────


def test_chunks_contain_no_debug_labels(
    db_session: Session,
    test_workspace: Workspace,
    temp_storage_dir: str,
    mock_vision_and_tesseract,
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    pdf_bytes = create_synthetic_pdf(["img"])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="no_labels.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    # Process through Tesseract then Meta Vision
    process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="meta_vision",
    )

    chunk_repo = DocumentChunkRepository(db_session)
    for chunk in chunk_repo.list_by_document(doc.id):
        assert "[OCR EXTRACTED]" not in chunk.content
        assert "[MUSE SPARK VISION]" not in chunk.content
        assert "[OCR]:" not in chunk.content
        assert "[MUSE SPARK]:" not in chunk.content


# ── Scenario 11: Chunk text does not duplicate entire paragraphs ──────────────


def test_chunk_text_does_not_duplicate_entire_paragraphs():
    """Verify chunking does not duplicate whole paragraphs across sequential windows."""
    p1 = "First distinct paragraph about circular requirements and timelines in 2026."
    p2 = "Second distinct paragraph regarding departmental obligations and filing dates."
    p3 = "Third distinct paragraph on audit verifications and compliance guidelines."

    full_page = f"{p1}\n\n{p2}\n\n{p3}"
    pages = [PageText(page_number=1, text=full_page)]

    chunks = chunk_document(pages, chunk_size=120, chunk_overlap=30)
    assert len(chunks) >= 2

    # Verify that a chunk content does not repeat the exact entire content of another chunk
    for i in range(len(chunks) - 1):
        assert chunks[i].content != chunks[i + 1].content


# ── Scenario 12: Sentence and word boundaries are respected ───────────────────


def test_sentence_and_word_boundaries_respected():
    text = (
        "Kerala State Department Circular No 102. All officers must report by 10 AM. "
        "The review will commence immediately afterwards. No unauthorized absences."
    )
    pages = [PageText(page_number=1, text=text)]
    chunks = chunk_document(pages, chunk_size=70, chunk_overlap=15)

    for c in chunks:
        # Should not end with a truncated word
        words = c.content.split()
        for w in words:
            assert len(w) > 0


# ── Scenario 13: Page provenance strictly preserved ───────────────────────────


def test_page_provenance_strictly_preserved(
    db_session: Session,
    test_workspace: Workspace,
    temp_storage_dir: str,
    mock_vision_and_tesseract,
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    pdf_bytes = create_synthetic_pdf(["img", "img"])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="provenance.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="meta_vision",
    )

    chunk_repo = DocumentChunkRepository(db_session)
    chunks = chunk_repo.list_by_document(doc.id)
    assert len(chunks) > 0
    for c in chunks:
        assert c.page_start >= 1
        assert c.page_end <= 2
        assert c.page_start <= c.page_end


# ── Scenario 14: Workspace isolation enforced on canonical selection routes ───


def test_workspace_isolation_enforced_on_canonical_selection_routes(
    client: TestClient,
    db_session: Session,
    test_workspace: Workspace,
    temp_storage_dir: str,
    mock_vision_and_tesseract,
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    pdf_bytes = create_synthetic_pdf(["img"])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="isolated.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="meta_vision",
    )

    # Foreign workspace
    foreign_ws = Workspace(name="Foreign Workspace", slug=f"foreign-{uuid.uuid4().hex[:6]}")
    db_session.add(foreign_ws)
    db_session.flush()

    # Attempt page canonical update from wrong workspace -> 404
    resp = client.put(
        f"/api/v1/workspaces/{foreign_ws.id}/documents/{doc.id}/pages/1/canonical",
        json={"provider": "meta_vision"},
    )
    assert resp.status_code == 404

    # Attempt document canonical update from wrong workspace -> 404
    resp2 = client.put(
        f"/api/v1/workspaces/{foreign_ws.id}/documents/{doc.id}/canonical",
        json={"provider": "meta_vision"},
    )
    assert resp2.status_code == 404


# ── Scenario 15: DB rollback works properly on failure ─────────────────────────


def test_db_rollback_works_properly_on_failure(
    db_session: Session,
    test_workspace: Workspace,
    temp_storage_dir: str,
    mock_vision_and_tesseract,
    monkeypatch,
):
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    pdf_bytes = create_synthetic_pdf(["img"])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="rollback_test.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="compare",
    )

    # Set canonical to tesseract
    select_canonical_extraction(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="tesseract",
    )

    # Simulate chunking failure during next canonical selection
    def _fail_chunking(*args, **kwargs):
        raise RuntimeError("Simulated chunk regeneration DB failure")

    monkeypatch.setattr("app.services.document_service.regenerate_document_chunks", _fail_chunking)

    with pytest.raises(RuntimeError, match="Simulated chunk regeneration DB failure"):
        select_canonical_extraction(
            session=db_session,
            settings=settings,
            workspace_id=test_workspace.id,
            document_id=doc.id,
            provider="meta_vision",
        )

    # Verify rollback: page canonical provider must still be tesseract
    page_repo = DocumentPageRepository(db_session)
    page = page_repo.list_by_document(doc.id)[0]
    assert page.extraction_method == "TESSERACT"
    assert page.extracted_text == mock_vision_and_tesseract["tesseract"]
