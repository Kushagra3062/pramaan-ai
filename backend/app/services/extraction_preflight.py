"""Pre-flight sizing for document extraction.

Counts the model calls an extraction run would make *before* any request is sent. The
eligibility rules deliberately mirror ``extraction_orchestrator.run_document_extraction``
(COMPLETED page, non-empty cleaned canonical text, single-page chunk of the same workspace and
document, non-blank content); parity is covered by tests. Read-only.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.repositories.document_repo import DocumentPageRepository
from app.ingestion.chunking import clean_canonical_text
from app.models.document import DocumentChunk


def count_eligible_chunks(session: Session, workspace_id: uuid.UUID, document_id: uuid.UUID) -> int:
    pages = DocumentPageRepository(session).list_by_document(document_id)
    eligible_pages = {
        p.page_number
        for p in pages
        if p.extraction_status == "COMPLETED" and clean_canonical_text(p.extracted_text or "")
    }
    if not eligible_pages:
        return 0
    chunks = session.execute(
        select(DocumentChunk.page_start, DocumentChunk.page_end, DocumentChunk.content).where(
            DocumentChunk.workspace_id == workspace_id,
            DocumentChunk.document_id == document_id,
        )
    ).all()
    return sum(
        1
        for start, end, content in chunks
        if start == end and start in eligible_pages and content and content.strip()
    )
