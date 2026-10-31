"""
Milestone 3.1.1 Chunking Refinement Test Suite.

Verifies:
1. Safe word boundaries (never begins or ends in the middle of a normal word like "checking" -> "hecking").
2. Controlled overlap within the same page without partial-word fragments.
3. Strict page-aware provenance (chunk.page_start == chunk.page_end).
4. Malayalam Unicode integrity, combining mark preservation, and grapheme protection.
5. Canonical text consistency between initial ingestion and chunk regeneration.
"""

from __future__ import annotations

import unicodedata

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.repositories.document_repo import (
    DocumentChunkRepository,
    DocumentPageRepository,
    PageExtractionAttemptRepository,
)
from app.ingestion.chunking import (
    PageText,
    _adjust_combining_boundary,
    _is_combining_mark,
    chunk_document,
    clean_canonical_text,
)
from app.models.workspace import Workspace
from app.services.document_service import (
    regenerate_document_chunks,
    select_canonical_extraction,
    upload_document,
)
from tests.helpers import create_synthetic_pdf

# ── 1. Word Boundaries Unit Tests ─────────────────────────────────────────────


def test_chunk_boundary_preserves_word_checking_no_hecking():
    """
    Verify that when a chunk boundary or overlap falls near the word 'checking',
    the next chunk starts with the complete word 'checking' and never a partial fragment 'hecking'.
    """
    prefix = "Initial section describing operational controls and compliance requirements. "
    # Craft text where target boundary lands precisely inside 'checking'
    text = prefix + "We are now checking every transaction carefully for verification."
    page = PageText(page_number=1, text=text)

    # Use a chunk size that lands inside 'checking'
    # prefix is ~78 chars; 'checking' is at indices 93..101
    chunks = chunk_document([page], chunk_size=95, chunk_overlap=30)
    assert len(chunks) >= 2

    # Verify no chunk starts with 'hecking' or ends with 'check'
    for chunk in chunks:
        assert not chunk.content.startswith("hecking")
        assert not chunk.content.startswith("ecking")
        assert not chunk.content.endswith("check")
        assert not chunk.content.endswith("checki")

    # The chunk containing 'checking' must have the complete word
    all_words = " ".join(c.content for c in chunks).split()
    assert "checking" in all_words
    assert "hecking" not in all_words


def test_chunk_boundaries_at_ordinary_english_whitespace():
    """Verify boundaries cleanly break at whitespace and words are not severed."""
    text = "Alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike."
    page = PageText(page_number=1, text=text)

    chunks = chunk_document([page], chunk_size=35, chunk_overlap=10)
    assert len(chunks) > 1

    expected_words = {"Alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet", "kilo", "lima", "mike."}
    for chunk in chunks:
        words = chunk.content.split()
        for w in words:
            # Every word in every chunk must be an expected complete word
            assert w in expected_words


def test_chunk_boundaries_at_sentence_endings():
    """Verify sentence terminators (. ! ?) are preferred cut boundaries."""
    sent1 = "The first complete regulation establishes reporting timelines."
    sent2 = "The second regulation outlines the required supervisory roles."
    text = f"{sent1} {sent2}"
    page = PageText(page_number=1, text=text)

    # Size large enough for sent1 + a few chars of sent2, so sent1 terminator should be chosen
    chunks = chunk_document([page], chunk_size=len(sent1) + 15, chunk_overlap=10)
    assert len(chunks) >= 2
    assert chunks[0].content.endswith(".")
    assert chunks[0].content == sent1


def test_long_words_exceeding_target_size_forced_split_progress():
    """
    Verify unbroken tokens exceeding target size split safely without an infinite loop
    and progress through the entire string.
    """
    long_token = "X" * 350
    text = f"Start {long_token} End"
    page = PageText(page_number=1, text=text)

    chunks = chunk_document([page], chunk_size=100, chunk_overlap=20)
    assert len(chunks) >= 3

    reconstructed = "".join(c.content.replace(" ", "") for c in chunks)
    assert "Start" in reconstructed
    assert "End" in reconstructed
    # Monotonically increasing indices
    for i, chunk in enumerate(chunks):
        assert chunk.chunk_index == i


def test_no_infinite_loop_when_natural_boundaries_are_absent():
    """Verify unbroken repeated sequence terminates reliably."""
    unbroken = "ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 20  # 520 chars, no spaces
    page = PageText(page_number=1, text=unbroken)

    chunks = chunk_document([page], chunk_size=150, chunk_overlap=30)
    assert len(chunks) >= 3
    # Check that each chunk advances forward
    assert chunks[0].content != chunks[1].content


# ── 2. Controlled Overlap Unit Tests ──────────────────────────────────────────


def test_overlap_begins_at_complete_word():
    """Verify overlap start always aligns to a complete word boundary."""
    text = (
        "Document authentication standard procedure requires mandatory verification "
        "and supervisory sign-off before operational deployment commences."
    )
    page = PageText(page_number=1, text=text)

    chunks = chunk_document([page], chunk_size=70, chunk_overlap=25)
    assert len(chunks) >= 2

    for chunk in chunks[1:]:
        # First word of the subsequent chunk must be a full, valid word
        first_word = chunk.content.split()[0]
        assert first_word in text.split()


def test_repeated_generation_is_deterministic():
    """Verify repeated chunk generation produces identical content, hashes, and counts."""
    text = (
        "Deterministic chunk generation guarantees reproducibility across multiple runs. "
        "Every chunk must maintain identical cryptographic SHA-256 hashes."
    )
    pages = [PageText(page_number=1, text=text)]

    run1 = chunk_document(pages, chunk_size=80, chunk_overlap=20)
    run2 = chunk_document(pages, chunk_size=80, chunk_overlap=20)

    assert len(run1) == len(run2)
    for c1, c2 in zip(run1, run2, strict=True):
        assert c1.chunk_index == c2.chunk_index
        assert c1.content == c2.content
        assert c1.content_hash == c2.content_hash
        assert c1.page_start == c2.page_start
        assert c1.page_end == c2.page_end


def test_overlap_remains_strictly_within_same_page():
    """Verify overlap never pulls content from page 1 into page 2 or vice versa."""
    p1_text = "Page 1: First operational protocol regarding safety and incident logging."
    p2_text = "Page 2: Second operational protocol regarding budget approval and auditing."
    pages = [
        PageText(page_number=1, text=p1_text),
        PageText(page_number=2, text=p2_text),
    ]

    chunks = chunk_document(pages, chunk_size=50, chunk_overlap=15)
    assert len(chunks) >= 2

    for chunk in chunks:
        if chunk.page_start == 1:
            assert "Page 2" not in chunk.content
            assert "budget" not in chunk.content
        elif chunk.page_start == 2:
            assert "Page 1" not in chunk.content
            assert "safety" not in chunk.content


# ── 3. Page Provenance Unit Tests ─────────────────────────────────────────────


def test_two_short_pages_generate_separate_page_aware_chunks():
    """
    Verify that two short pages are NEVER combined into a single chunk.
    Both must produce chunks with page_start == page_end.
    """
    pages = [
        PageText(page_number=1, text="Short notice for department staff regarding schedule."),
        PageText(page_number=2, text="Second notice regarding annual leave submission deadlines."),
    ]

    # chunk_size is 1000 (much larger than both texts combined)
    chunks = chunk_document(pages, chunk_size=1000, chunk_overlap=200)

    # Must produce 2 chunks (one per page), not 1 combined chunk
    assert len(chunks) == 2
    assert chunks[0].page_start == 1
    assert chunks[0].page_end == 1
    assert chunks[0].chunk_index == 0
    assert "Short notice" in chunks[0].content
    assert "Second notice" not in chunks[0].content

    assert chunks[1].page_start == 2
    assert chunks[1].page_end == 2
    assert chunks[1].chunk_index == 1
    assert "Second notice" in chunks[1].content
    assert "Short notice" not in chunks[1].content


def test_long_page_generates_multiple_chunks_with_same_page_provenance():
    """Verify a long page produces multiple chunks, all having page_start == page_end == page_num."""
    long_content = "This is a detailed operational instruction with thorough analysis. " * 30
    pages = [PageText(page_number=3, text=long_content)]

    chunks = chunk_document(pages, chunk_size=400, chunk_overlap=100)
    assert len(chunks) >= 3

    for i, chunk in enumerate(chunks):
        assert chunk.chunk_index == i
        assert chunk.page_start == 3
        assert chunk.page_end == 3


def test_chunk_indices_remain_globally_ordered():
    """Verify chunk indices strictly increment from 0 to N-1 across all pages."""
    pages = [
        PageText(page_number=1, text="Page 1 sentence one. Page 1 sentence two with details."),
        PageText(page_number=2, text="Page 2 sentence one. Page 2 sentence two with details."),
        PageText(page_number=3, text="Page 3 sentence one. Page 3 sentence two with details."),
    ]

    chunks = chunk_document(pages, chunk_size=35, chunk_overlap=10)
    assert len(chunks) >= 3
    for i, chunk in enumerate(chunks):
        assert chunk.chunk_index == i


def test_short_meaningful_pages_are_not_discarded():
    """Verify a short but meaningful page (e.g. 35 chars) is retained as a valid chunk."""
    short_text = "Executive Order No. 42 approved."  # 32 characters
    pages = [PageText(page_number=1, text=short_text)]

    chunks = chunk_document(pages, chunk_size=1000, chunk_overlap=200, min_chunk_chars=20)
    assert len(chunks) == 1
    assert chunks[0].content == short_text
    assert chunks[0].page_start == 1
    assert chunks[0].page_end == 1


# ── 4. Malayalam Unicode & Combining Marks Unit Tests ─────────────────────────


def test_malayalam_combining_mark_detection():
    """Verify _is_combining_mark identifies Malayalam viramas, vowel signs, and joiners."""
    # Virama (chandrakkala) ്: Mn
    assert _is_combining_mark("\u0d4d") is True
    # Vowel sign aa ാ: Mc
    assert _is_combining_mark("\u0d3e") is True
    # Vowel sign i ി: Mc
    assert _is_combining_mark("\u0d3f") is True
    # Anusvara ം: Mc
    assert _is_combining_mark("\u0d02") is True
    # Zero-width joiner \u200D
    assert _is_combining_mark("\u200d") is True
    # Regular consonant ക: Lo
    assert _is_combining_mark("ക") is False
    # English letter
    assert _is_combining_mark("A") is False


def test_malayalam_combining_boundary_adjustment():
    """Verify _adjust_combining_boundary does not sever base consonant from combining mark."""
    # 'കി' is 'ക' (U+0D15) + 'ി' (U+0D3F)
    text = "ക\u0d3f"
    # If cut points to index 1 (between ക and ി), it should advance to 2
    assert _adjust_combining_boundary(text, 1) == 2


def test_malayalam_words_remain_intact_without_midword_cuts():
    """Verify Malayalam text with whitespace and dandas chunks with intact words."""
    malayalam_text = (
        "പ്രമാണം പരിശോധന ടെസ്റ്റ് ഫയൽ 03. സ്കാൻ ചെയ്ത മലയാളം രേഖ രേഖ നമ്പർ 003. "
        "പ്രതിമാസ റിപ്പോർട്ട് സമർപ്പിക്കേണ്ട അവസാന തീയതി 30 നവംബർ 2026 ആണ്. "
        "എല്ലാ വകുപ്പുകളും അവരുടെ പ്രവർത്തന റിപ്പോർട്ടുകൾ ഈ തീയതിക്ക് മുമ്പായി സമർപ്പിക്കണം."
    )
    page = PageText(page_number=1, text=malayalam_text)

    chunks = chunk_document([page], chunk_size=120, chunk_overlap=30)
    assert len(chunks) >= 2

    # Verify no chunk starts or ends with an isolated combining mark
    for chunk in chunks:
        first_char = chunk.content[0]
        assert unicodedata.category(first_char) not in ("Mn", "Mc", "Me")
        assert not chunk.content.startswith("\u0d4d")  # virama
        assert not chunk.content.startswith("\u0d3e")  # vowel sign

    # The reconstructed content must contain key Malayalam terms
    full = " ".join(c.content for c in chunks)
    assert "പ്രമാണം" in full
    assert "റിപ്പോർട്ട്" in full
    assert "നവംബർ 2026" in full


# ── 5. Canonical Text Consistency Tests (Service Level) ───────────────────────


def test_clean_canonical_text_strips_debug_markers():
    """Verify debug labels are thoroughly stripped from canonical text."""
    dirty = (
        "[OCR EXTRACTED]: Official circular text.\n"
        "[MUSE SPARK VISION]: Official circular text with vision corrections.\n"
        "[OCR] Header line."
    )
    cleaned = clean_canonical_text(dirty)
    assert "[OCR EXTRACTED]" not in cleaned
    assert "[MUSE SPARK VISION]" not in cleaned
    assert "[OCR]" not in cleaned


def test_initial_upload_and_regeneration_produce_identical_chunks(
    db_session: Session, test_workspace: Workspace, temp_storage_dir: str
):
    """
    Verify that initial document upload and subsequent chunk regeneration produce
    100% identical chunk contents, hashes, and counts for a digital PDF.
    """
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    page_1_text = "Page 1: Operational standard operating procedure regarding data protection."
    page_2_text = "Page 2: Incident notification timelines and escalation chain of custody."
    pdf_bytes = create_synthetic_pdf([page_1_text, page_2_text])

    # 1. Initial upload
    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="consistency_test.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    chunk_repo = DocumentChunkRepository(db_session)
    initial_chunks = chunk_repo.list_by_document(doc.id)
    assert len(initial_chunks) == 2  # 1 chunk per page

    initial_hashes = [c.content_hash for c in initial_chunks]
    initial_contents = [c.content for c in initial_chunks]

    # Verify each chunk has page_start == page_end
    assert initial_chunks[0].page_start == 1
    assert initial_chunks[0].page_end == 1
    assert initial_chunks[1].page_start == 2
    assert initial_chunks[1].page_end == 2

    # 2. Trigger regeneration
    new_count = regenerate_document_chunks(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
    )
    assert new_count == len(initial_chunks)

    regenerated_chunks = chunk_repo.list_by_document(doc.id)
    regenerated_hashes = [c.content_hash for c in regenerated_chunks]
    regenerated_contents = [c.content for c in regenerated_chunks]

    # Content and hashes must match identically
    assert initial_hashes == regenerated_hashes
    assert initial_contents == regenerated_contents


def test_only_selected_canonical_text_is_chunked(
    db_session: Session, test_workspace: Workspace, temp_storage_dir: str
):
    """
    Verify that when multiple provider attempts exist, only the selected canonical
    transcription enters the generated chunks, with zero contamination from alternative providers.
    """
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    pdf_bytes = create_synthetic_pdf(["Scanned placeholder page"])

    doc = upload_document(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        filename="scanned_can_test.pdf",
        content_type="application/pdf",
        file_data=pdf_bytes,
    )

    page_repo = DocumentPageRepository(db_session)
    pages = page_repo.list_by_document(doc.id)
    page_1 = pages[0]

    # Record 2 separate attempts
    att_repo = PageExtractionAttemptRepository(db_session)
    att_repo.record_attempt(
        document_id=doc.id,
        document_page_id=page_1.id,
        page_number=1,
        provider="tesseract",
        extracted_text="TESSERACT CLEAN TRANSCRIPTION: Budget approved for fiscal year 2026.",
        status="COMPLETED",
    )
    att_repo.record_attempt(
        document_id=doc.id,
        document_page_id=page_1.id,
        page_number=1,
        provider="meta_vision",
        extracted_text="MUSE SPARK CLEAN TRANSCRIPTION: Budget approved for fiscal year 2026 with high accuracy.",
        status="COMPLETED",
    )
    db_session.flush()

    # Select Tesseract as canonical
    select_canonical_extraction(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="tesseract",
    )

    chunk_repo = DocumentChunkRepository(db_session)
    tess_chunks = chunk_repo.list_by_document(doc.id)
    assert len(tess_chunks) >= 1
    assert any("TESSERACT CLEAN TRANSCRIPTION" in c.content for c in tess_chunks)
    assert not any("MUSE SPARK" in c.content for c in tess_chunks)

    # Now switch canonical to Meta Vision
    select_canonical_extraction(
        session=db_session,
        settings=settings,
        workspace_id=test_workspace.id,
        document_id=doc.id,
        provider="meta_vision",
    )

    vision_chunks = chunk_repo.list_by_document(doc.id)
    assert len(vision_chunks) >= 1
    assert any("MUSE SPARK CLEAN TRANSCRIPTION" in c.content for c in vision_chunks)
    assert not any("TESSERACT CLEAN TRANSCRIPTION" in c.content for c in vision_chunks)
