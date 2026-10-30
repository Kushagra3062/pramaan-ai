"""
Deterministic, word-and-sentence boundary preserving text chunker.

Improvements over basic character windowing:
1. Prefers paragraph breaks, sentence punctuation (. ! ? \n), and whitespace.
2. Avoids splitting in the middle of words unless forced by chunk_size constraint.
3. Preserves Malayalam Unicode strings and multi-byte script characters.
4. Preserves 1-based source page ranges, deterministic ordering, and SHA-256 hashes.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ChunkResult:
    """A single chunk produced from document text."""

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
    return len(text.split())


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _find_clean_boundary(text: str, target_idx: int, min_idx: int) -> int:
    """
    Search backwards from target_idx down to min_idx for natural text boundaries:
    1. Paragraph break (\n\n)
    2. Line break (\n)
    3. Sentence terminators (. ! ?) followed by whitespace
    4. General whitespace (space, tab)
    Returns target_idx if no boundary is found within the window.
    """
    if target_idx >= len(text):
        return len(text)

    # 1. Look for double newline
    p_break = text.rfind("\n\n", min_idx, target_idx)
    if p_break != -1:
        return p_break + 2

    # 2. Look for single newline
    n_break = text.rfind("\n", min_idx, target_idx)
    if n_break != -1:
        return n_break + 1

    # 3. Look for sentence boundary (. ! ?)
    for sep in (". ", "! ", "? ", "। ", ".\n"):
        s_break = text.rfind(sep, min_idx, target_idx)
        if s_break != -1:
            return s_break + len(sep)

    # 4. Look for whitespace
    w_break = text.rfind(" ", min_idx, target_idx)
    if w_break != -1:
        return w_break + 1

    return target_idx


def chunk_document(
    pages: list[PageText],
    *,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    min_chunk_chars: int = 20,
) -> list[ChunkResult]:
    """
    Split document pages into overlapping chunks adhering to natural word and sentence boundaries.
    Preserves exact 1-based page provenance and deterministic chunk hashes.
    """
    if not pages:
        return []

    if chunk_overlap >= chunk_size:
        chunk_overlap = chunk_size // 4

    char_to_page: list[int] = []
    full_text_parts: list[str] = []

    for page in pages:
        text = page.text.strip()
        if not text:
            continue
        if full_text_parts:
            full_text_parts.append("\n\n")
            char_to_page.extend([char_to_page[-1], page.page_number])
        full_text_parts.append(text)
        char_to_page.extend([page.page_number] * len(text))

    full_text = "".join(full_text_parts)
    if len(full_text.strip()) < min_chunk_chars:
        return []

    chunks: list[ChunkResult] = []
    start = 0
    chunk_index = 0
    text_len = len(full_text)

    while start < text_len:
        # Proposed end
        ideal_end = min(start + chunk_size, text_len)

        if ideal_end < text_len:
            # Find natural word / sentence break within the last 30% of the chunk window
            search_min = max(start + (chunk_size // 2), start + 50)
            actual_end = _find_clean_boundary(full_text, ideal_end, search_min)
        else:
            actual_end = text_len

        chunk_text = full_text[start:actual_end].strip()

        if len(chunk_text) >= min_chunk_chars:
            start_idx = min(start, len(char_to_page) - 1)
            end_idx = min(actual_end - 1, len(char_to_page) - 1)
            page_start = char_to_page[start_idx]
            page_end = char_to_page[end_idx]

            chunks.append(
                ChunkResult(
                    chunk_index=chunk_index,
                    content=chunk_text,
                    page_start=page_start,
                    page_end=page_end,
                    token_count=_estimate_tokens(chunk_text),
                    content_hash=_content_hash(chunk_text),
                )
            )
            chunk_index += 1

        if actual_end >= text_len:
            break

        # Calculate next start position using overlap, adjusted to clean word boundary
        ideal_next_start = max(start + 1, actual_end - chunk_overlap)
        actual_next_start = _find_clean_boundary(full_text, ideal_next_start, max(start + 1, ideal_next_start - 50))

        if actual_next_start <= start:
            actual_next_start = actual_end

        start = actual_next_start

    logger.info(
        "chunking_boundary_preserved",
        extra={
            "event": "chunking.completed",
            "total_chars": text_len,
            "chunk_count": len(chunks),
        },
    )
    return chunks
