"""Offline tests for deterministic evidence verification (Phase 4B.3A)."""

import copy
import unicodedata
import uuid

from app.ingestion.evidence_verifier import (
    CanonicalChunkRef,
    ChunkMatchOutcome,
    EvidenceMatchStatus,
    find_matching_canonical_chunks,
    normalize_evidence_text,
    verify_evidence_quote,
)

EN = "The department must submit the report by 30 November."
ML = "വകുപ്പ് റിപ്പോർട്ട് സമർപ്പിക്കണം"  # contains virama + vowel signs


def _chunk(idx, content, page=1, cid=None, end=None):
    return CanonicalChunkRef(
        chunk_id=cid or uuid.UUID(int=idx + 1),
        chunk_index=idx,
        page_start=page,
        page_end=end if end is not None else page,
        content=content,
    )


# A. Basic
def test_exact_match():
    r = verify_evidence_quote(f"Intro. {EN} Outro.", EN)
    assert r.status is EvidenceMatchStatus.EXACT_MATCH and r.is_exact_match
    assert r.method


def test_missing_quote_not_found():
    r = verify_evidence_quote(EN, "Something else entirely")
    assert r.status is EvidenceMatchStatus.NOT_FOUND and r.reason


def test_empty_quote_invalid():
    assert verify_evidence_quote(EN, "").status is EvidenceMatchStatus.INVALID_INPUT


def test_whitespace_only_quote_invalid():
    assert verify_evidence_quote(EN, " \n\t ").status is EvidenceMatchStatus.INVALID_INPUT


def test_empty_source_not_found():
    assert verify_evidence_quote("", EN).status is EvidenceMatchStatus.NOT_FOUND


def test_non_string_invalid():
    assert verify_evidence_quote(None, EN).status is EvidenceMatchStatus.INVALID_INPUT  # type: ignore[arg-type]


def test_quote_at_start_and_end():
    assert verify_evidence_quote("Alpha beta gamma", "Alpha beta").is_exact_match
    assert verify_evidence_quote("Alpha beta gamma", "beta gamma").is_exact_match


def test_case_mismatch_rejected():
    assert not verify_evidence_quote(EN, EN.lower()).is_exact_match


def test_punctuation_mismatch_rejected():
    assert not verify_evidence_quote(EN, EN.rstrip(".")+"!").is_exact_match
    assert not verify_evidence_quote("a, b", "a b").is_exact_match


# B. Unicode / Malayalam
def test_malayalam_exact_match():
    assert verify_evidence_quote(f"പ്രമാണം: {ML}.", ML).is_exact_match


def test_malayalam_marks_preserved():
    out = normalize_evidence_text(ML)
    assert out == unicodedata.normalize("NFC", ML)
    assert "്" in out and "െ" in out or "ി" in out


def test_mixed_language_match():
    src = f"Section 4: {ML} (by 30 November)"
    assert verify_evidence_quote(src, f"{ML} (by 30 November)").is_exact_match


def test_nfc_equivalents_match():
    nfd = unicodedata.normalize("NFD", "café")
    assert nfd != "café"
    assert verify_evidence_quote(f"Visit the {nfd} now", "café").is_exact_match


def test_no_transliteration_or_deletion():
    out = normalize_evidence_text(ML)
    assert out.isascii() is False and len(out) == len(unicodedata.normalize("NFC", ML))
    assert not verify_evidence_quote(ML, "vakuppu").is_exact_match


def test_no_nfkc_compatibility_folding():
    assert normalize_evidence_text("ﬁle") == "ﬁle"  # fi ligature stays
    assert not verify_evidence_quote("ﬁle", "file").is_exact_match
    assert normalize_evidence_text("①") == "①"


def test_zwj_zwnj_preserved():
    s = "ന‍ന‌"
    assert "‍" in normalize_evidence_text(s) and "‌" in normalize_evidence_text(s)


# C. Whitespace
def test_newline_vs_space():
    src = "The department must submit\n the report by 30 November."
    assert verify_evidence_quote(src, EN).is_exact_match


def test_multiple_spaces():
    assert verify_evidence_quote("a    b   c", "a b c").is_exact_match


def test_tabs_and_linebreaks():
    assert verify_evidence_quote("a\t\tb\r\n\r\nc", "a b c").is_exact_match


def test_leading_trailing_whitespace():
    assert verify_evidence_quote("a b", "  \n a b \t ").is_exact_match
    assert normalize_evidence_text("  x \n y  ") == "x y"


# D. Chunk mapping
def test_single_chunk_mapping():
    chunks = [_chunk(0, "Intro text here."), _chunk(1, f"More. {EN}")]
    page = "Intro text here. " + f"More. {EN}"
    r = find_matching_canonical_chunks(EN, 1, page, chunks)
    assert r.outcome is ChunkMatchOutcome.SINGLE_CHUNK_MATCH
    assert r.matching_chunk_ids == (chunks[1].chunk_id,)


def test_other_page_chunk_ignored():
    chunks = [_chunk(0, EN, page=2)]
    r = find_matching_canonical_chunks(EN, 1, EN, chunks)
    assert r.outcome is ChunkMatchOutcome.QUOTE_IN_PAGE_NO_CHUNK
    assert r.matching_chunk_ids == ()


def test_multi_page_chunk_not_accepted():
    chunks = [_chunk(0, EN, page=1, end=2)]
    r = find_matching_canonical_chunks(EN, 1, EN, chunks)
    assert r.outcome is ChunkMatchOutcome.QUOTE_IN_PAGE_NO_CHUNK


def test_quote_in_page_but_no_chunk():
    r = find_matching_canonical_chunks(EN, 1, EN, [_chunk(0, "unrelated")])
    assert r.outcome is ChunkMatchOutcome.QUOTE_IN_PAGE_NO_CHUNK


def test_quote_not_in_page():
    r = find_matching_canonical_chunks("absent quote", 1, EN, [_chunk(0, EN)])
    assert r.outcome is ChunkMatchOutcome.QUOTE_NOT_IN_PAGE


def test_quote_split_across_chunks_not_accepted():
    a, b = "The department must submit", "the report by 30 November."
    r = find_matching_canonical_chunks(EN, 1, f"{a} {b}", [_chunk(0, a), _chunk(1, b)])
    assert r.outcome is ChunkMatchOutcome.QUOTE_IN_PAGE_NO_CHUNK


def test_multiple_matches_deterministic_order():
    c0, c1, c2 = _chunk(0, f"x {EN}"), _chunk(1, f"{EN} y"), _chunk(2, "none")
    r1 = find_matching_canonical_chunks(EN, 1, f"x {EN} y", [c2, c1, c0])
    r2 = find_matching_canonical_chunks(EN, 1, f"x {EN} y", [c0, c1, c2])
    assert r1.outcome is ChunkMatchOutcome.MULTIPLE_CHUNK_MATCHES
    assert r1.matching_chunk_ids == r2.matching_chunk_ids == (c0.chunk_id, c1.chunk_id)


def test_duplicate_chunk_ids_deduplicated():
    c = _chunk(0, EN)
    r = find_matching_canonical_chunks(EN, 1, EN, [c, c, _chunk(0, EN, cid=c.chunk_id)])
    assert r.outcome is ChunkMatchOutcome.SINGLE_CHUNK_MATCH
    assert r.matching_chunk_ids == (c.chunk_id,)


def test_stable_ordering_by_chunk_index_not_input_order():
    cs = [_chunk(i, EN) for i in (5, 1, 3)]
    r = find_matching_canonical_chunks(EN, 1, EN, cs)
    assert r.matching_chunk_ids == tuple(c.chunk_id for c in sorted(cs, key=lambda c: c.chunk_index))


def test_no_fuzzy_matching():
    near = "The department must submit the report by 30 Novemer."
    assert not verify_evidence_quote(EN, near).is_exact_match
    r = find_matching_canonical_chunks(near, 1, EN, [_chunk(0, EN)])
    assert r.outcome is ChunkMatchOutcome.QUOTE_NOT_IN_PAGE


def test_malayalam_chunk_mapping():
    r = find_matching_canonical_chunks(ML, 3, ML, [_chunk(0, f"{ML}.", page=3)])
    assert r.outcome is ChunkMatchOutcome.SINGLE_CHUNK_MATCH


def test_invalid_inputs_for_mapping():
    c = [_chunk(0, EN)]
    assert find_matching_canonical_chunks("", 1, EN, c).outcome is ChunkMatchOutcome.INVALID_INPUT
    assert find_matching_canonical_chunks(EN, 0, EN, c).outcome is ChunkMatchOutcome.INVALID_INPUT


# E. Provenance safety
def test_non_canonical_chunk_inputs_rejected():
    class Attempt:  # e.g. a PageExtractionAttempt-like object
        chunk_id, chunk_index, page_start, page_end, content = uuid.uuid4(), 0, 1, 1, EN

    r = find_matching_canonical_chunks(EN, 1, EN, [Attempt()])  # type: ignore[list-item]
    assert r.outcome is ChunkMatchOutcome.INVALID_INPUT
    r = find_matching_canonical_chunks(EN, 1, EN, [{"content": EN}])  # type: ignore[list-item]
    assert r.outcome is ChunkMatchOutcome.INVALID_INPUT


def test_inputs_not_mutated():
    chunks = [_chunk(1, f"a\n{EN}"), _chunk(0, "zzz")]
    before = copy.deepcopy(chunks)
    page, quote = f"zzz a\n{EN}", f"  {EN} "
    find_matching_canonical_chunks(quote, 1, page, chunks)
    assert chunks == before and page == f"zzz a\n{EN}" and quote == f"  {EN} "
