"""Shared FastAPI dependencies.

NOTE: the application has no authentication or workspace-membership authorization yet.
Workspace/document scoping below is path consistency only, NOT user authorization. When
authentication is added, put the identity/membership check in ``get_scoped_document`` (and
sibling dependencies) so every document-scoped route inherits it from one place.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.repositories.document_repo import DocumentRepository
from app.models.document import Document
from app.models.enums import DocumentProcessingStatus


def get_scoped_document(
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    session: Session = Depends(get_db),
) -> Document:
    """Workspace-scoped document lookup; 404 for missing, cross-workspace or deleted documents."""
    doc = DocumentRepository(session).get_by_workspace_and_id(workspace_id, document_id)
    if (
        doc is None
        or doc.deleted_at is not None
        or doc.processing_status
        in (DocumentProcessingStatus.DELETING.value, DocumentProcessingStatus.DELETED.value)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return doc
