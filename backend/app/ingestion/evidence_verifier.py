"""
Deterministic evidence verification for model-proposed quotes.

Pure Python: no database, network, file or model access.

Invariant:
A quote is verified only if, after NFC + whitespace normalization, it is an
exact substring of the canonical source text. Fuzzy similarity is never
accepted. An exact match proves textual presence only; it does NOT mean the
model's interpretation is correct or human-approved, and this module never
touches review statuses.

Trusted provenance (page number, chunk IDs, canonical text) is supplied by the
caller from backend records; the model never selects it.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

# str patterns match Unicode whitespace (incl. NBSP, line/paragraph separators).
_WHITESPACE_RE = re.compile(r"\s+")

MATCH_METHOD_EXACT_NORMALIZED = "EXACT_NORMALIZED_SUBSTRING"


def normalize_evidence_text(text: str) -> str:
    """NFC-normalize, collapse whitespace runs to one space, and strip.

    Deliberately NOT NFKC, no casefolding, no punctuation/mark removal, so
    Malayalam vowel signs, viramas and ZWJ/ZWNJ are preserved.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    return _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFC", text)).strip()


class EvidenceMatchStatus(StrEnum):
    EXACT_MATCH = "EXACT_MATCH"
    NOT_FOUND = "NOT_FOUND"
    INVALID_INPUT = "INVALID_INPUT"


@dataclass(frozen=True)
class EvidenceVerificationResult:
    status: EvidenceMatchStatus
    method: str | None = None
    reason: str | None = None

    @property
    def is_exact_match(self) -> bool:
        return self.status is EvidenceMatchStatus.EXACT_MATCH


def _invalid(reason: str) -> EvidenceVerificationResult:
    return EvidenceVerificationResult(EvidenceMatchStatus.INVALID_INPUT, reason=reason)


def verify_evidence_quote(source_text: str, source_quote: str) -> EvidenceVerificationResult:
    """Check that ``source_quote`` appears exactly in ``source_text`` after safe normalization."""
    if not isinstance(source_text, str) or not isinstance(source_quote, str):
        return _invalid("source_text and source_quote must be strings")
    quote = normalize_evidence_text(source_quote)
    if not quote:
        return _invalid("source_quote is empty after normalization")
    source = normalize_evidence_text(source_text)
    if not source:
        return EvidenceVerificationResult(
            EvidenceMatchStatus.NOT_FOUND, reason="source text is empty"
        )
    if quote in source:
        return EvidenceVerificationResult(
            EvidenceMatchStatus.EXACT_MATCH, method=MATCH_METHOD_EXACT_NORMALIZED
        )
    return EvidenceVerificationResult(
        EvidenceMatchStatus.NOT_FOUND, reason="quote not found in source text"
    )


@dataclass(frozen=True)
class CanonicalChunkRef:
    """Lightweight view of a persisted canonical DocumentChunk (trusted backend data)."""

    chunk_id: uuid.UUID
    chunk_index: int
    page_start: int
    page_end: int
    content: str


class ChunkMatchOutcome(StrEnum):
    SINGLE_CHUNK_MATCH = "SINGLE_CHUNK_MATCH"
    MULTIPLE_CHUNK_MATCHES = "MULTIPLE_CHUNK_MATCHES"
    QUOTE_NOT_IN_PAGE = "QUOTE_NOT_IN_PAGE"
    QUOTE_IN_PAGE_NO_CHUNK = "QUOTE_IN_PAGE_NO_CHUNK"
    INVALID_INPUT = "INVALID_INPUT"


@dataclass(frozen=True)
class ChunkMatchResult:
    outcome: ChunkMatchOutcome
    matching_chunk_ids: tuple[uuid.UUID, ...] = ()
    reason: str | None = None


def find_matching_canonical_chunks(
    source_quote: str,
    page_number: int,
    page_text: str,
    canonical_chunks: Iterable[CanonicalChunkRef],
) -> ChunkMatchResult:
    """Map a quote to canonical chunks of one page, without choosing among multiples.

    ``page_text`` is the canonical ``document_pages.extracted_text`` for
    ``page_number``. Only chunks with ``page_start == page_end == page_number``
    are considered; the whole normalized quote must lie inside one chunk.
    Results are ordered by (chunk_index, chunk_id) and deduplicated by chunk_id.
    """
    if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
        return ChunkMatchResult(ChunkMatchOutcome.INVALID_INPUT, reason="invalid page_number")
    chunks: Sequence[object] = tuple(canonical_chunks)
    if not all(isinstance(c, CanonicalChunkRef) for c in chunks):
        return ChunkMatchResult(
            ChunkMatchOutcome.INVALID_INPUT,
            reason="canonical_chunks must be CanonicalChunkRef instances",
        )

    page_check = verify_evidence_quote(page_text, source_quote)
    if page_check.status is EvidenceMatchStatus.INVALID_INPUT:
        return ChunkMatchResult(ChunkMatchOutcome.INVALID_INPUT, reason=page_check.reason)
    if not page_check.is_exact_match:
        return ChunkMatchResult(
            ChunkMatchOutcome.QUOTE_NOT_IN_PAGE, reason="quote not found in canonical page text"
        )

    eligible = sorted(
        (c for c in chunks if c.page_start == page_number and c.page_end == page_number),  # type: ignore[union-attr]
        key=lambda c: (c.chunk_index, str(c.chunk_id)),  # type: ignore[union-attr]
    )
    seen: set[uuid.UUID] = set()
    matches: list[uuid.UUID] = []
    for chunk in eligible:
        if chunk.chunk_id in seen:  # type: ignore[union-attr]
            continue
        seen.add(chunk.chunk_id)  # type: ignore[union-attr]
        if verify_evidence_quote(chunk.content, source_quote).is_exact_match:  # type: ignore[union-attr]
            matches.append(chunk.chunk_id)  # type: ignore[union-attr]

    if not matches:
        return ChunkMatchResult(
            ChunkMatchOutcome.QUOTE_IN_PAGE_NO_CHUNK,
            reason="quote is in canonical page text but within no single supplied chunk",
        )
    outcome = (
        ChunkMatchOutcome.SINGLE_CHUNK_MATCH
        if len(matches) == 1
        else ChunkMatchOutcome.MULTIPLE_CHUNK_MATCHES
    )
    return ChunkMatchResult(outcome, matching_chunk_ids=tuple(matches))
