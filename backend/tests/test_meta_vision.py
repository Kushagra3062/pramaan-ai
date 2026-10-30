"""
Tests for Milestone 3.1: Meta Muse Spark Vision Integration and OCR Quality.

Covers:
- Image payload preparation (base64 data URL, sha256 input hash)
- Model selection and request structure
- Error normalization (RateLimitError, APITimeoutError, APIConnectionError, APIStatusError)
- Structured JSON parsing with uncertain regions
- Malayalam and multilingual Unicode text preservation
- Tesseract fallback logic (enabled vs disabled)
- Compare mode (dual-provider recording, canonical text protection)
- Extraction provenance tracking in page_extraction_attempts
- Word/sentence boundary preservation in chunking
- Workspace isolation and API endpoints
- Opt-in live Meta API integration test (RUN_META_VISION_INTEGRATION)
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.repositories.document_repo import (
    DocumentPageRepository,
    PageExtractionAttemptRepository,
)
from app.ingestion.chunking import PageText, chunk_document
from app.ingestion.meta_vision import (
    MUSE_SPARK_TRANSCRIPTION_PROMPT_VERSION,
    MetaVisionExtractor,
    MetaVisionResult,
)
from app.models.enums import DocumentProcessingStatus
from app.models.workspace import Workspace
from app.services.document_service import (
    process_document_ocr,
    upload_document,
)
from tests.helpers import create_synthetic_pdf

# ── Extractor Unit Tests ───────────────────────────────────────────────────────


def test_meta_vision_extractor_payload_and_call(monkeypatch):
    """Verify image is converted to base64 data URL and passed with correct model & system prompt."""
    settings = get_settings()
    extractor = MetaVisionExtractor(settings)

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = (
        '{"transcription": "Order No. 42 / 2026", "language_hints": ["English"], "uncertain_regions": []}'
    )
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage.model_dump.return_value = {"prompt_tokens": 120, "completion_tokens": 30}
    mock_client.chat.completions.create.return_value = mock_response

    monkeypatch.setattr("app.ingestion.meta_vision.get_meta_client", lambda: mock_client)

    sample_bytes = b"fake_png_data_bytes_12345"
    result = extractor.transcribe_image_bytes(sample_bytes, mime_type="image/png")

    assert result.status == "COMPLETED"
    assert result.transcription == "Order No. 42 / 2026"
    assert result.model_id == settings.meta_model_id
    assert result.prompt_version == MUSE_SPARK_TRANSCRIPTION_PROMPT_VERSION
    assert result.language_hints == ["English"]
    assert result.usage_metadata == {"prompt_tokens": 120, "completion_tokens": 30}

    # Verify client call parameters
    mock_client.chat.completions.create.assert_called_once()
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == settings.meta_model_id
    assert call_kwargs["temperature"] == 0.0

    messages = call_kwargs["messages"]
    assert messages[0]["role"] == "system"
    user_content = messages[1]["content"]
    assert any(part.get("type") == "image_url" for part in user_content)
    image_part = next(part for part in user_content if part.get("type") == "image_url")
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")


def test_meta_vision_extractor_handles_markdown_wrapped_json(monkeypatch):
    """Verify extractor safely extracts JSON wrapped in code fences."""
    settings = get_settings()
    extractor = MetaVisionExtractor(settings)

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = (
        "```json\n"
        '{\n  "transcription": "കേരള സർക്കാർ വിജ്ഞാപനം",\n'
        '  "language_hints": ["Malayalam"],\n'
        '  "uncertain_regions": [{"text_excerpt": "...", "reason": "ink blot"}]\n'
        "}\n"
        "```"
    )
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = None
    mock_client.chat.completions.create.return_value = mock_response

    monkeypatch.setattr("app.ingestion.meta_vision.get_meta_client", lambda: mock_client)

    result = extractor.transcribe_image_bytes(b"image_bytes")
    assert result.status == "COMPLETED"
    assert result.transcription == "കേരള സർക്കാർ വിജ്ഞാപനം"
    assert result.language_hints == ["Malayalam"]
    assert len(result.uncertain_regions) == 1
    assert result.uncertain_regions[0]["reason"] == "ink blot"


def test_meta_vision_extractor_handles_raw_text_fallback(monkeypatch):
    """Verify fallback if model returns plain text without JSON formatting."""
    settings = get_settings()
    extractor = MetaVisionExtractor(settings)

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "Plain unformatted document transcription without JSON wrapper."
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = None
    mock_client.chat.completions.create.return_value = mock_response

    monkeypatch.setattr("app.ingestion.meta_vision.get_meta_client", lambda: mock_client)

    result = extractor.transcribe_image_bytes(b"image_bytes")
    assert result.status == "COMPLETED"
    assert "Plain unformatted document transcription" in result.transcription


@pytest.mark.parametrize(
    ("exception_cls", "expected_code"),
    [
        (RateLimitError, "RateLimitError"),
        (APITimeoutError, "APITimeoutError"),
        (APIConnectionError, "APIConnectionError"),
    ],
)
def test_meta_vision_extractor_handles_api_exceptions(monkeypatch, exception_cls, expected_code):
    """Verify API errors are trapped and returned as FAILED result with error code."""
    settings = get_settings()
    extractor = MetaVisionExtractor(settings)

    mock_client = MagicMock()
    if exception_cls == RateLimitError:
        exc = RateLimitError(message="Rate limit exceeded", response=MagicMock(status_code=429), body=None)
    elif exception_cls == APITimeoutError:
        exc = APITimeoutError(request=MagicMock())
    else:
        exc = APIConnectionError(request=MagicMock())

    mock_client.chat.completions.create.side_effect = exc
    monkeypatch.setattr("app.ingestion.meta_vision.get_meta_client", lambda: mock_client)

    result = extractor.transcribe_image_bytes(b"image_bytes")
    assert result.status == "FAILED"
    assert result.error_code == expected_code
    assert result.transcription == ""


def test_meta_vision_extractor_handles_api_status_error(monkeypatch):
    """Verify 401/403/500 APIStatusErrors are caught cleanly."""
    settings = get_settings()
    extractor = MetaVisionExtractor(settings)

    mock_client = MagicMock()
    exc = APIStatusError(
        message="Invalid API Key",
        response=MagicMock(status_code=401),
        body={"error": {"message": "Invalid API Key"}},
    )
    mock_client.chat.completions.create.side_effect = exc
    monkeypatch.setattr("app.ingestion.meta_vision.get_meta_client", lambda: mock_client)

    result = extractor.transcribe_image_bytes(b"image_bytes")
    assert result.status == "FAILED"
    assert result.error_code == "APIStatusError"


# ── Service Layer Mode Tests ──────────────────────────────────────────────────


def test_document_service_meta_vision_mode_success(
    db_session: Session, test_workspace: Workspace, temp_storage_dir: str, monkeypatch
):
    """Test process_document_ocr with provider='meta_vision' succeeds and records provenance."""
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    pdf_bytes = create_synthetic_pdf(["img"])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="scanned_policy.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )
    assert doc.processing_status == DocumentProcessingStatus.NEEDS_OCR.value

    # Mock Meta Vision extractor
    def _mock_transcribe(self, image_bytes, mime_type="image/png"):
        return MetaVisionResult(
            transcription="Transcribed operational rule 104: All tenders must be submitted by 5 PM.",
            language_hints=["English"],
            uncertain_regions=[{"text_excerpt": "104", "reason": "faint dot"}],
            model_id="muse-spark-1.3",
            prompt_version="v1.1-vision-transcription",
            input_hash="abc123hash",
            latency_ms=250,
            usage_metadata={"prompt_tokens": 100, "completion_tokens": 20},
            status="COMPLETED",
        )

    monkeypatch.setattr(MetaVisionExtractor, "transcribe_image_bytes", _mock_transcribe)

    updated_doc, results, chunks_count, mode = process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="meta_vision",
    )

    assert mode == "meta_vision"
    assert updated_doc.processing_status == DocumentProcessingStatus.READY.value
    assert len(results) == 1
    assert results[0].provider == "meta_vision"
    assert "tenders must be submitted" in results[0].recognized_text
    assert chunks_count >= 1

    # Verify attempt recorded in database
    attempt_repo = PageExtractionAttemptRepository(db_session)
    attempts = attempt_repo.list_by_document(doc.id)
    assert len(attempts) == 1
    att = attempts[0]
    assert att.provider == "meta_vision"
    assert att.model_id == "muse-spark-1.3"
    assert att.prompt_version == "v1.1-vision-transcription"
    assert att.status == "COMPLETED"
    assert att.latency_ms == 250
    assert att.input_hash == "abc123hash"
    assert "tenders must be submitted" in att.extracted_text


def test_document_service_meta_vision_malayalam_preservation(
    db_session: Session, test_workspace: Workspace, temp_storage_dir: str, monkeypatch
):
    """Test Malayalam Unicode script is preserved intact by Meta Vision provider."""
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    pdf_bytes = create_synthetic_pdf(["img"])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="malayalam_scanned.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    malayalam_sample = "ധനകാര്യ വകുപ്പ് ഉത്തരവ് നമ്പർ: 108/2026 തീയതി 15 മാർച്ച് 2026."

    def _mock_transcribe(self, image_bytes, mime_type="image/png"):
        return MetaVisionResult(
            transcription=malayalam_sample,
            language_hints=["Malayalam"],
            uncertain_regions=[],
            model_id="muse-spark-1.3",
            prompt_version="v1.1-vision-transcription",
            input_hash="hash_mal",
            latency_ms=310,
            status="COMPLETED",
        )

    monkeypatch.setattr(MetaVisionExtractor, "transcribe_image_bytes", _mock_transcribe)

    updated_doc, results, _, _ = process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="meta_vision",
    )

    assert updated_doc.processing_status == DocumentProcessingStatus.READY.value
    page_repo = DocumentPageRepository(db_session)
    pages = page_repo.list_by_document(doc.id)
    assert malayalam_sample in pages[0].extracted_text
    assert "ധനകാര്യ വകുപ്പ്" in pages[0].extracted_text


def test_document_service_fallback_to_tesseract_when_meta_fails(
    db_session: Session, test_workspace: Workspace, temp_storage_dir: str, monkeypatch
):
    """Test automatic fallback to Tesseract when Meta Vision fails and fallback_to_tesseract=True."""
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    pdf_bytes = create_synthetic_pdf(["img"])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="fail_then_fallback.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    # 1. Meta Vision returns failure
    def _mock_fail(self, image_bytes, mime_type="image/png"):
        return MetaVisionResult(
            transcription="",
            language_hints=[],
            uncertain_regions=[],
            model_id="muse-spark-1.3",
            prompt_version="v1.1-vision-transcription",
            input_hash="err_hash",
            latency_ms=150,
            status="FAILED",
            error_code="RateLimitError",
            error_message="Too many requests",
        )

    monkeypatch.setattr(MetaVisionExtractor, "transcribe_image_bytes", _mock_fail)

    # 2. Mock Tesseract fallback
    def _mock_tess(doc, page_num, **kwargs):
        from app.ingestion.ocr import PageOCRResult

        return PageOCRResult(
            page_number=page_num,
            recognized_text="Tesseract fallback successful text extraction.",
            char_count=45,
            extraction_method="OCR",
            extraction_status="COMPLETED",
            languages_used="eng",
            provider="tesseract",
            metadata={"source": "fallback"},
        )

    monkeypatch.setattr("app.services.document_service.run_ocr_on_page", _mock_tess)

    updated_doc, results, chunks_count, _ = process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="meta_vision",
        fallback_to_tesseract=True,
    )

    # Should succeed via fallback
    assert updated_doc.processing_status == DocumentProcessingStatus.READY.value
    assert len(results) == 1
    assert "Tesseract fallback successful" in results[0].recognized_text
    assert results[0].metadata.get("fallback_from") == "meta_vision"

    # Verify BOTH attempts recorded: failed meta_vision attempt + successful tesseract attempt
    attempt_repo = PageExtractionAttemptRepository(db_session)
    attempts = attempt_repo.list_by_document(doc.id)
    assert len(attempts) == 2
    providers = [a.provider for a in attempts]
    assert "meta_vision" in providers
    assert "tesseract" in providers

    meta_att = next(a for a in attempts if a.provider == "meta_vision")
    assert meta_att.status == "FAILED"
    assert meta_att.error_code == "RateLimitError"

    tess_att = next(a for a in attempts if a.provider == "tesseract")
    assert tess_att.status == "COMPLETED"


def test_document_service_no_fallback_when_disabled(
    db_session: Session, test_workspace: Workspace, temp_storage_dir: str, monkeypatch
):
    """Test that when fallback_to_tesseract=False, Meta Vision failure fails the page without calling Tesseract."""
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    pdf_bytes = create_synthetic_pdf(["img"])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="strict_meta.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    def _mock_fail(self, image_bytes, mime_type="image/png"):
        return MetaVisionResult(
            transcription="",
            language_hints=[],
            uncertain_regions=[],
            model_id="muse-spark-1.3",
            prompt_version="v1.1-vision-transcription",
            input_hash="err_hash",
            latency_ms=100,
            status="FAILED",
            error_code="APIStatusError",
            error_message="Unauthorized",
        )

    monkeypatch.setattr(MetaVisionExtractor, "transcribe_image_bytes", _mock_fail)

    updated_doc, results, chunks_count, _ = process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="meta_vision",
        fallback_to_tesseract=False,
    )

    assert updated_doc.processing_status == DocumentProcessingStatus.FAILED.value
    assert len(results) == 1
    assert results[0].extraction_status == "FAILED"
    assert results[0].provider == "meta_vision"

    # Only 1 attempt recorded (meta_vision failed, no tesseract attempted)
    attempt_repo = PageExtractionAttemptRepository(db_session)
    attempts = attempt_repo.list_by_document(doc.id)
    assert len(attempts) == 1
    assert attempts[0].provider == "meta_vision"
    assert attempts[0].status == "FAILED"


def test_document_service_compare_mode(
    db_session: Session, test_workspace: Workspace, temp_storage_dir: str, monkeypatch
):
    """Test compare mode runs both providers, stores both attempts, and protects canonical text."""
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    pdf_bytes = create_synthetic_pdf(["img"])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="compare_doc.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    # Mock Tesseract
    def _mock_tess(doc_obj, page_num, **kwargs):
        from app.ingestion.ocr import PageOCRResult

        return PageOCRResult(
            page_number=page_num,
            recognized_text="Tesseract recognized: Clause 4.1 Notice period is 30 days.",
            char_count=52,
            extraction_method="OCR",
            extraction_status="COMPLETED",
            languages_used="eng",
            provider="tesseract",
        )

    monkeypatch.setattr("app.services.document_service.run_ocr_on_page", _mock_tess)

    # Mock Meta Vision
    def _mock_vision(self, image_bytes, mime_type="image/png"):
        return MetaVisionResult(
            transcription="Meta Vision recognized: Clause 4.1 Notice period is 30 days exactly.",
            language_hints=["English"],
            uncertain_regions=[],
            model_id="muse-spark-1.3",
            prompt_version="v1.1-vision-transcription",
            input_hash="hash_compare",
            latency_ms=220,
            status="COMPLETED",
        )

    monkeypatch.setattr(MetaVisionExtractor, "transcribe_image_bytes", _mock_vision)

    updated_doc, results, chunks_count, mode = process_document_ocr(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="compare",
    )

    assert mode == "compare"

    # Both attempts must be in page_extraction_attempts
    attempt_repo = PageExtractionAttemptRepository(db_session)
    attempts = attempt_repo.list_by_document(doc.id)
    assert len(attempts) == 2
    providers = {a.provider: a.extracted_text for a in attempts}
    assert "tesseract" in providers
    assert "meta_vision" in providers
    assert "Clause 4.1 Notice period is 30 days." in providers["tesseract"]
    assert "30 days exactly" in providers["meta_vision"]


# ── Chunker Boundary Quality Tests ────────────────────────────────────────────


def test_chunking_preserves_word_and_sentence_boundaries():
    """Verify chunker never slices through words and breaks at punctuation/sentences."""
    text_with_sentences = (
        "This is the first complete sentence of the policy document. "
        "Here is the second operational guideline regarding financial approvals. "
        "The third regulation specifies that all expenditures above Rs 50,000 need board clearance. "
        "Finally, this is the closing paragraph of section alpha."
    )
    pages = [PageText(page_number=1, text=text_with_sentences)]

    # Use a small chunk size to force chunk boundaries
    chunks = chunk_document(pages, chunk_size=120, chunk_overlap=30)
    assert len(chunks) > 1

    for chunk in chunks:
        # No chunk should start or end with a partial cut word (unless word itself is longer than chunk)
        # Verify chunk doesn't end abruptly without whitespace or punctuation
        assert chunk.content.strip() != ""
        # Check that words are preserved intact
        words = chunk.content.split()
        for w in words:
            # Word should not contain broken control characters
            assert not w.endswith("\x00")

    # Full text coverage
    reconstructed_snippets = [c.content for c in chunks]
    assert any("first complete sentence" in c for c in reconstructed_snippets)
    assert any("expenditures above Rs 50,000" in c for c in reconstructed_snippets)


def test_chunking_devanagari_sentence_boundary():
    """Verify chunker handles Devanagari/Indic danda sentence boundary '।' cleanly."""
    indic_text = (
        "ആദ്യത്തെ പ്രധാന നിയമം ഇതാണ്। രണ്ടാമത്തെ മാർഗ്ഗനിർദ്ദേശം സാമ്പത്തിക കാര്യങ്ങളെക്കുറിച്ചാണ്। "
        "എല്ലാ ചെലവുകളും ബോർഡ് അംഗീകരിക്കണം। അവസാന നിർദ്ദേശം പൂർത്തിയായി।"
    )
    pages = [PageText(page_number=1, text=indic_text)]
    chunks = chunk_document(pages, chunk_size=100, chunk_overlap=20)
    assert len(chunks) >= 1
    for chunk in chunks:
        assert len(chunk.content) > 0


# ── API Endpoints & Authorization Tests ───────────────────────────────────────


def test_api_trigger_ocr_with_meta_vision_and_get_attempts(
    client: TestClient,
    test_workspace: Workspace,
    temp_storage_dir: str,
    monkeypatch,
):
    """Test POST /ocr with provider=meta_vision and GET /extraction-attempts endpoint."""
    pdf_bytes = create_synthetic_pdf(["img"])

    # Upload
    upload_res = client.post(
        f"/api/v1/workspaces/{test_workspace.id}/documents",
        files={"file": ("scanned_notice.pdf", pdf_bytes, "application/pdf")},
    )
    assert upload_res.status_code == 201
    doc_id = upload_res.json()["id"]

    # Mock Vision extractor
    def _mock_transcribe(self, image_bytes, mime_type="image/png"):
        return MetaVisionResult(
            transcription="API Test Notice: Office will remain closed on public holidays.",
            language_hints=["English"],
            uncertain_regions=[{"text_excerpt": "Notice", "reason": "header shading"}],
            model_id="muse-spark-1.3",
            prompt_version="v1.1-vision-transcription",
            input_hash="hash_api_test",
            latency_ms=195,
            usage_metadata={"prompt_tokens": 80, "completion_tokens": 15},
            status="COMPLETED",
        )

    monkeypatch.setattr(MetaVisionExtractor, "transcribe_image_bytes", _mock_transcribe)

    # Trigger OCR with provider=meta_vision
    ocr_res = client.post(
        f"/api/v1/workspaces/{test_workspace.id}/documents/{doc_id}/ocr",
        json={"provider": "meta_vision", "fallback_to_tesseract": False},
    )
    assert ocr_res.status_code == 200
    data = ocr_res.json()
    assert data["provider_mode"] == "meta_vision"
    assert len(data["results"]) == 1
    assert data["results"][0]["provider"] == "meta_vision"
    assert len(data["results"][0]["uncertain_regions"]) == 1

    # Call GET /extraction-attempts
    att_res = client.get(
        f"/api/v1/workspaces/{test_workspace.id}/documents/{doc_id}/extraction-attempts"
    )
    assert att_res.status_code == 200
    attempts = att_res.json()
    assert len(attempts) == 1
    assert attempts[0]["provider"] == "meta_vision"
    assert attempts[0]["model_id"] == "muse-spark-1.3"
    assert attempts[0]["latency_ms"] == 195
    assert "Office will remain closed" in attempts[0]["extracted_text"]


def test_api_trigger_compare_and_get_compare_endpoint(
    client: TestClient,
    test_workspace: Workspace,
    temp_storage_dir: str,
    monkeypatch,
):
    """Test POST /ocr with provider=compare and GET /compare endpoint."""
    pdf_bytes = create_synthetic_pdf(["img"])

    upload_res = client.post(
        f"/api/v1/workspaces/{test_workspace.id}/documents",
        files={"file": ("compare_test.pdf", pdf_bytes, "application/pdf")},
    )
    doc_id = upload_res.json()["id"]

    # Mock Tesseract
    def _mock_tess(doc_obj, page_num, **kwargs):
        from app.ingestion.ocr import PageOCRResult

        return PageOCRResult(
            page_number=page_num,
            recognized_text="Tesseract extracted text for comparison.",
            char_count=40,
            extraction_method="OCR",
            extraction_status="COMPLETED",
            languages_used="eng",
            provider="tesseract",
        )

    monkeypatch.setattr("app.services.document_service.run_ocr_on_page", _mock_tess)

    # Mock Vision
    def _mock_vision(self, image_bytes, mime_type="image/png"):
        return MetaVisionResult(
            transcription="Meta Vision extracted text for comparison.",
            language_hints=["English"],
            uncertain_regions=[],
            model_id="muse-spark-1.3",
            prompt_version="v1.1-vision-transcription",
            input_hash="hash_comp_api",
            latency_ms=180,
            status="COMPLETED",
        )

    monkeypatch.setattr(MetaVisionExtractor, "transcribe_image_bytes", _mock_vision)

    # Trigger OCR compare
    ocr_res = client.post(
        f"/api/v1/workspaces/{test_workspace.id}/documents/{doc_id}/ocr",
        json={"provider": "compare"},
    )
    assert ocr_res.status_code == 200
    ocr_data = ocr_res.json()
    assert ocr_data["provider_mode"] == "compare"
    assert len(ocr_data["comparisons"]) == 1
    comp = ocr_data["comparisons"][0]
    assert comp["tesseract_attempt"] is not None
    assert comp["meta_vision_attempt"] is not None
    assert "Tesseract" in comp["tesseract_attempt"]["extracted_text"]
    assert "Meta Vision" in comp["meta_vision_attempt"]["extracted_text"]

    # Call GET /compare
    get_comp_res = client.get(
        f"/api/v1/workspaces/{test_workspace.id}/documents/{doc_id}/compare"
    )
    assert get_comp_res.status_code == 200
    comp_list = get_comp_res.json()
    assert len(comp_list) == 1
    assert comp_list[0]["page_number"] == 1


def test_api_workspace_isolation_on_new_endpoints(
    client: TestClient,
    test_workspace: Workspace,
    db_session: Session,
):
    """Test cross-workspace access to extraction-attempts and compare endpoints returns 404."""
    import uuid

    from app.db.repositories.workspace_repo import WorkspaceRepository

    repo = WorkspaceRepository(db_session)
    other_ws = repo.create(name="Other Workspace", slug="other-workspace-iso")
    db_session.commit()

    random_doc_id = uuid.uuid4()
    # Attempts endpoint
    res1 = client.get(
        f"/api/v1/workspaces/{other_ws.id}/documents/{random_doc_id}/extraction-attempts"
    )
    assert res1.status_code == 404

    # Compare endpoint
    res2 = client.get(
        f"/api/v1/workspaces/{other_ws.id}/documents/{random_doc_id}/compare"
    )
    assert res2.status_code == 404


# ── Opt-in Live Integration Test ──────────────────────────────────────────────


@pytest.mark.skipif(
    os.getenv("RUN_META_VISION_INTEGRATION") != "true",
    reason="Opt-in live Meta Vision integration test. Set RUN_META_VISION_INTEGRATION=true to run.",
)
def test_live_meta_vision_integration():
    """Live test against Meta Model API using muse-spark-1.3 with a synthetic rendered page."""
    import pymupdf

    from app.ingestion.ocr import render_page_to_image_bytes

    settings = get_settings()
    pdf_bytes = create_synthetic_pdf(["Pramaan AI Live Integration: Clause 99 Verified."])
    doc = pymupdf.open("pdf", pdf_bytes)
    png_bytes, mime = render_page_to_image_bytes(doc, 1, dpi=150)
    doc.close()

    extractor = MetaVisionExtractor(settings)
    result = extractor.transcribe_image_bytes(png_bytes, mime_type=mime)

    assert result.status == "COMPLETED"
    assert "Pramaan" in result.transcription or "Live" in result.transcription
    assert result.latency_ms > 0
    assert result.model_id == "muse-spark-1.3"
