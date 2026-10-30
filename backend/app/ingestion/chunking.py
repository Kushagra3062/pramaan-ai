"""
Deterministic text chunking service.

Splits extracted document text into overlapping chunks while preserving
page provenance and stable ordering.  No embeddings or vector operations
are performed here.
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
    """Rough token estimate using whitespace splitting."""
    return len(text.split())


def _content_hash(text: str) -> str:
    """Deterministic SHA-256 hash of chunk content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_document(
    pages: list[PageText],
    *,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    min_chunk_chars: int = 20,
) -> list[ChunkResult]:
    """
    Split page texts into overlapping character-level chunks.

    The algorithm concatenates all page texts (preserving page boundaries),
    then produces fixed-size overlapping windows.  Each chunk records which
    pages contributed to it.

    Parameters
    ----------
    pages:
        Ordered list of page texts (1-based page numbers).
    chunk_size:
        Target chunk size in characters.
    chunk_overlap:
        Overlap between consecutive chunks in characters.
    min_chunk_chars:
        Chunks shorter than this are discarded.

    Returns
    -------
    list[ChunkResult]
        Ordered list of chunks with page provenance.
    """
    if not pages:
        return []
    if chunk_overlap >= chunk_size:
        chunk_overlap = chunk_size // 4
        logger.warning(
            "chunk_overlap_clamped",
            extra={
                "event": "chunking.overlap_clamped",
                "new_overlap": chunk_overlap,
            },
        )

    # Build a flat text with a mapping from character offset -> page number
    char_to_page: list[int] = []
    full_text_parts: list[str] = []

    for page in pages:
        text = page.text.strip()
        if not text:
            continue
        # Add a page separator (newline) between pages
        if full_text_parts:
            full_text_parts.append("\n")
            char_to_page.append(char_to_page[-1] if char_to_page else page.page_number)
        full_text_parts.append(text)
        char_to_page.extend([page.page_number] * len(text))

    full_text = "".join(full_text_parts)

    if len(full_text.strip()) < min_chunk_chars:
        return []

    # Sliding window chunking
    chunks: list[ChunkResult] = []
    step = chunk_size - chunk_overlap
    start = 0
    chunk_index = 0

    while start < len(full_text):
        end = min(start + chunk_size, len(full_text))
        chunk_text = full_text[start:end].strip()

        if len(chunk_text) < min_chunk_chars:
            break

        # Determine page range for this chunk
        # Clamp to valid indices
        start_idx = min(start, len(char_to_page) - 1)
        end_idx = min(end - 1, len(char_to_page) - 1)
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
        start += step

    logger.info(
        "chunking_completed",
        extra={
            "event": "chunking.completed",
            "total_chars": len(full_text),
            "chunk_count": len(chunks),
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
        },
    )
    return chunks

