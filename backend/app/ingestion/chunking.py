"""
Deterministic, page-aware, word-and-sentence boundary preserving text chunker.

Key Architectural Guarantees:
1. Safe Word Boundaries:
   - Chunks never begin or end in the middle of a normal word.
   - Boundary preference: Paragraph breaks (\\n\\n) -> Sentence terminators (. ! ? । ॥) ->
     Line breaks (\\n) -> Whitespace (' ', '\\t').
   - Backward search window with forward fallback ensures complete words are never severed.
   - Unicode-safe forced-split strategy for genuinely oversized unbroken tokens.
2. Controlled Overlap:
   - Target overlap is character-based (chunk_overlap, default 200 chars).
   - Overlap start snaps to the beginning of a complete word; never leaves partial-word fragments
     (e.g., snaps to "checking", never "hecking").
   - Overlap occurs strictly within the same page; never crosses page boundaries.
3. Page-Aware Provenance:
   - Each PageText is processed independently.
   - Every chunk produced has page_start == page_end == page.page_number.
   - Globally sequential, 0-based chunk_index across all document pages.
   - Preserves document reading order.
4. Malayalam & Indic Script Safety:
   - Unicode combining marks (Mn, Mc, Me), viramas (്), vowel signs, and joiners (ZWJ/ZWNJ)
     are strictly protected against mid-grapheme splits.
   - Danda (।) and double danda (॥) recognized as sentence boundaries.
5. Canonical Integrity & Determinism:
   - Strips provider debug markers ([OCR EXTRACTED], [MUSE SPARK VISION]).
   - Deterministic SHA-256 content hashing.
   - Lightweight whitespace-based token_count estimate (no external tokenizer dependency).
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_DEBUG_MARKER_PATTERN = re.compile(
    r"^\[(?:OCR EXTRACTED|MUSE SPARK VISION|OCR|MUSE SPARK)\]:?\s*",
    re.MULTILINE | re.IGNORECASE,
)

# Sentence terminators: western + Indic dandas, followed by space or newline
_SENTENCE_TERMINATORS = (
    ". ",
    "! ",
    "? ",
    "। ",
    "॥ ",
    ".\n",
    "!\n",
    "?\n",
    "।\n",
    "॥\n",
    ";\n",
    ":\n",
)


def clean_canonical_text(text: str) -> str:
    """
    Strip out any provider debug headers or markers from text.
    Ensures that canonical page text and chunks are completely clean.
    """
    if not text:
        return ""
    cleaned = _DEBUG_MARKER_PATTERN.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


@dataclass
class ChunkResult:
    """
    A single chunk produced from document text.

    Attributes:
        chunk_index: Globally sequential 0-based index across the document.
        content: Clean canonical chunk text with complete words.
        page_start: 1-based source page number. For page-aware chunks, page_start == page_end.
        page_end: 1-based source page number. For page-aware chunks, page_start == page_end.
        token_count: Approximate whitespace-split word count estimate (not a BPE/WordPiece token count).
        content_hash: SHA-256 hexadecimal digest of the chunk content.
    """

    chunk_index: int
    content: str
    page_start: int  # 1-based
    page_end: int  # 1-based
    token_count: int  # approximate whitespace-split token count
    content_hash: str  # SHA-256 of the chunk content


@dataclass
class PageText:
    """Input: a page's text associated with its 1-based page number."""

    page_number: int
    text: str


def _estimate_tokens(text: str) -> int:
    """Approximate word count estimate based on whitespace splitting."""
    return len(text.split())


def _content_hash(text: str) -> str:
    """Deterministic SHA-256 hash of UTF-8 encoded text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_combining_mark(char: str) -> bool:
    """
    Check if a character is a combining mark, virama, or zero-width joiner/non-joiner.
    Categories:
      Mn: Nonspacing Mark (e.g. Malayalam virama ്, anusvara ം)
      Mc: Spacing Mark (e.g. Malayalam vowel signs ാ, ി, ീ, െ)
      Me: Enclosing Mark
      ZWJ (\\u200D) / ZWNJ (\\u200C)
    """
    return unicodedata.category(char) in ("Mn", "Mc", "Me") or char in ("\u200c", "\u200d")


def _adjust_combining_boundary(text: str, idx: int) -> int:
    """Ensure split index does not sever a base letter from following combining marks."""
    while 0 < idx < len(text) and _is_combining_mark(text[idx]):
        idx += 1
    return idx


def _find_chunk_end(
    text: str,
    *,
    start: int,
    chunk_size: int,
    min_chunk_chars: int = 20,
) -> int:
    """
    Find a clean end boundary for a chunk starting at `start`.

    Boundary search hierarchy:
    1. If the remainder of the text from start is within (chunk_size + min_chunk_chars),
       absorb the entire remainder so no trailing orphan fragment is created.
    2. Paragraph break (\\n\\n) within the backward search window.
    3. Sentence terminators (. ! ? । ॥) within the backward search window.
    4. Line break (\\n) within the backward search window.
    5. Whitespace (' ', '\\t') within the backward search window.
    6. Safe forward search: if no boundary exists backwards, search forward for the next whitespace
       so that the current word is completed without being split in half.
    7. Forced split fallback: for genuinely oversized unbroken tokens exceeding chunk_size,
       split at target_idx with combining-mark protection.
    """
    text_len = len(text)
    remaining = text_len - start

    # Absorb remainder if it would leave a tiny orphan fragment
    if remaining <= chunk_size + min_chunk_chars:
        return text_len

    def _finalize_end(cand: int) -> int:
        cand = _adjust_combining_boundary(text, cand)
        if text_len - cand < min_chunk_chars:
            return text_len
        return max(start + 1, cand)

    target_idx = start + chunk_size
    search_min = max(start + min_chunk_chars, target_idx - (chunk_size // 2))

    # 1. Look for double newline (paragraph break)
    p_break = text.rfind("\n\n", search_min, target_idx)
    if p_break != -1:
        return _finalize_end(p_break + 2)

    # 2. Look for sentence terminators (. ! ? । ॥)
    best_sent_break = -1
    for sep in _SENTENCE_TERMINATORS:
        s_break = text.rfind(sep, search_min, target_idx)
        if s_break != -1:
            cand = s_break + len(sep)
            if cand > best_sent_break:
                best_sent_break = cand
    if best_sent_break != -1:
        return _finalize_end(best_sent_break)

    # 3. Look for single newline
    n_break = text.rfind("\n", search_min, target_idx)
    if n_break != -1:
        return _finalize_end(n_break + 1)

    # 4. Look for whitespace (' ', '\t') in backward window
    w_break = -1
    for i in range(target_idx, search_min - 1, -1):
        if i < text_len and text[i].isspace():
            w_break = i
            break
    if w_break != -1:
        return _finalize_end(w_break + 1)

    # Also search backward between start + min_chunk_chars and search_min if needed
    earlier_ws = -1
    for i in range(search_min, start + min_chunk_chars - 1, -1):
        if i < text_len and text[i].isspace():
            earlier_ws = i
            break
    if earlier_ws != -1:
        return _finalize_end(earlier_ws + 1)

    # 5. Safe forward search: find the next whitespace to finish a normal word
    # Search forward a sensible window (up to 30 chars or 10% of chunk_size) past target_idx
    forward_limit = min(text_len, target_idx + max(30, chunk_size // 10))
    for i in range(target_idx, forward_limit):
        if text[i].isspace():
            return _finalize_end(i + 1)

    # 6. Forced split fallback for genuinely oversized unbroken tokens
    forced = _adjust_combining_boundary(text, target_idx)
    if forced <= start:
        forced = min(text_len, start + chunk_size)
        forced = _adjust_combining_boundary(text, forced)

    return _finalize_end(forced)


def _find_overlap_start(
    text: str,
    *,
    prev_start: int,
    actual_end: int,
    chunk_overlap: int,
) -> int:
    """
    Calculate a safe start index for the next chunk on the same page.

    Requirements:
    - Guaranteed forward progress: next_start > prev_start.
    - Safe word boundary: chunk never begins in the middle of a word (e.g. "checking", not "hecking").
    - Snaps to sentence/paragraph breaks within overlap window if available.
    - Snaps to the start of a complete word if mid-word.
    - Returns actual_end if no clean overlap boundary exists.
    """
    if chunk_overlap <= 0 or actual_end >= len(text):
        return actual_end

    raw_target = actual_end - chunk_overlap
    target_start = max(prev_start + 1, raw_target)

    # 1. Look for paragraph break in the overlap window [target_start, actual_end]
    p_break = text.find("\n\n", target_start, actual_end)
    if p_break != -1:
        cand = p_break + 2
        while cand < actual_end and text[cand].isspace():
            cand += 1
        if prev_start < cand < actual_end:
            return _adjust_combining_boundary(text, cand)

    # 2. Look for sentence terminators in the overlap window
    best_sent_cand = -1
    for sep in _SENTENCE_TERMINATORS:
        s_break = text.find(sep, target_start, actual_end)
        if s_break != -1:
            cand = s_break + len(sep)
            while cand < actual_end and text[cand].isspace():
                cand += 1
            if prev_start < cand < actual_end:
                if best_sent_cand == -1 or cand < best_sent_cand:
                    best_sent_cand = cand
    if best_sent_cand != -1:
        return _adjust_combining_boundary(text, best_sent_cand)

    # 3. Check if target_start is in the middle of a normal word
    # An index i is mid-word if text[i] is non-space and text[i-1] is non-space
    is_mid_word = (
        target_start > 0
        and target_start < len(text)
        and not text[target_start - 1].isspace()
        and not text[target_start].isspace()
    )

    if is_mid_word:
        # Snap BACKWARD to the beginning of this word if within reasonable word length
        w_start = -1
        for i in range(target_start - 1, prev_start, -1):
            if text[i].isspace():
                w_start = i + 1
                break
        max_snap_back = max(50, chunk_overlap)
        if w_start != -1 and w_start > prev_start and (target_start - w_start) <= max_snap_back:
            target_start = w_start
        else:
            # If the token is oversized (exceeds max_snap_back), or no preceding space found,
            # snap forward to start of next word or keep target_start
            next_word_start = -1
            for i in range(target_start, actual_end):
                if text[i].isspace():
                    j = i
                    while j < actual_end and text[j].isspace():
                        j += 1
                    if prev_start < j < actual_end:
                        next_word_start = j
                    break
            if next_word_start != -1:
                target_start = next_word_start
            else:
                target_start = _adjust_combining_boundary(text, target_start)
    else:
        # Skip leading whitespace at target_start to align with the first character of the word
        while target_start < actual_end and text[target_start].isspace():
            target_start += 1

    target_start = _adjust_combining_boundary(text, target_start)

    # Guarantee forward progress
    if target_start <= prev_start or target_start >= actual_end:
        return actual_end

    return target_start


def chunk_document(
    pages: list[PageText],
    *,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    min_chunk_chars: int = 20,
) -> list[ChunkResult]:
    """
    Split document pages into page-aware, overlapping chunks adhering to natural word and sentence boundaries.

    Character-based configuration:
    - chunk_size: Target maximum chunk length in characters (not tokens).
    - chunk_overlap: Target overlap between adjacent chunks in characters (not tokens) on the same page.
    - min_chunk_chars: Minimum character length for a chunk or short meaningful page text to be retained.

    Page-aware provenance guarantees:
    - Each page is chunked independently.
    - For every chunk: page_start == page_end == page.page_number.
    - Chunks never combine content across different pages.
    - Monotonically increasing chunk_index (0, 1, 2, ...) across the document.
    - Overlap is applied only within the same page.
    - Complete words and Indic/Malayalam combining characters are strictly preserved.
    """
    if not pages:
        return []

    if chunk_overlap >= chunk_size:
        chunk_overlap = chunk_size // 4

    chunks: list[ChunkResult] = []
    chunk_index = 0

    for page in pages:
        page_text = clean_canonical_text(page.text)
        if not page_text or not page_text.strip():
            continue

        stripped_len = len(page_text.strip())
        if stripped_len < min_chunk_chars:
            # Respect min_chunk_chars for fragments that are too short to be meaningful
            continue

        page_len = len(page_text)
        start = 0

        while start < page_len:
            # Skip any leading whitespace at start of chunk
            while start < page_len and page_text[start].isspace():
                start += 1
            if start >= page_len:
                break

            actual_end = _find_chunk_end(
                page_text,
                start=start,
                chunk_size=chunk_size,
                min_chunk_chars=min_chunk_chars,
            )

            chunk_content = page_text[start:actual_end].strip()

            if len(chunk_content) >= min_chunk_chars:
                chunks.append(
                    ChunkResult(
                        chunk_index=chunk_index,
                        content=chunk_content,
                        page_start=page.page_number,
                        page_end=page.page_number,
                        token_count=_estimate_tokens(chunk_content),
                        content_hash=_content_hash(chunk_content),
                    )
                )
                chunk_index += 1

            if actual_end >= page_len:
                break

            next_start = _find_overlap_start(
                page_text,
                prev_start=start,
                actual_end=actual_end,
                chunk_overlap=chunk_overlap,
            )

            # Ensure forward progress
            if next_start <= start:
                next_start = actual_end

            start = next_start

    return chunks
