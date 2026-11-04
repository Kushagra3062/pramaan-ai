"""Workspace-scoped evidence retrieval for Investigate (FR-009, lexical stage).

Current method (``lexical-fts-v1``): PostgreSQL full-text rank over chunk
content with an exact-phrase boost, restricted to the requesting workspace and
to chunks of non-deleted documents. Vector re-ranking is intentionally absent:
chunk embeddings are not populated in the MVP, and the response labels the
method honestly instead of claiming hybrid retrieval.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.document import Document, DocumentChunk

RETRIEVAL_METHOD = "lexical-fts-v1"
DEFAULT_LIMIT = 8
MAX_LIMIT = 20


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    page_start: int
    page_end: int
    content: str
    rank: float


def retrieve_chunks(
    session: Session,
    workspace_id: uuid.UUID,
    query: str,
    *,
    document_ids: list[uuid.UUID] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[RetrievedChunk]:
    """Return top-ranked chunks. Read-only; empty query yields no rows."""
    cleaned = " ".join((query or "").split())
    if not cleaned:
        return []
    capped = max(1, min(limit, MAX_LIMIT))
    tsquery = func.plainto_tsquery("simple", cleaned)
    rank = func.ts_rank(func.to_tsvector("simple", DocumentChunk.content), tsquery)
    stmt = (
        select(DocumentChunk, Document.original_filename, rank.label("rank"))
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(
            DocumentChunk.workspace_id == workspace_id,
            Document.workspace_id == workspace_id,
            Document.deleted_at.is_(None),
            func.to_tsvector("simple", DocumentChunk.content).op("@@")(tsquery),
        )
    )
    if document_ids:
        stmt = stmt.where(DocumentChunk.document_id.in_(document_ids))
    stmt = stmt.order_by(rank.desc(), DocumentChunk.chunk_index.asc()).limit(capped)
    results: list[RetrievedChunk] = []
    for chunk, filename, chunk_rank in session.execute(stmt).all():
        boost = 0.5 if cleaned.casefold() in chunk.content.casefold() else 0.0
        results.append(
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                filename=filename,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                content=chunk.content,
                rank=round(float(chunk_rank or 0.0) + boost, 4),
            )
        )
    results.sort(key=lambda item: item.rank, reverse=True)
    return results
