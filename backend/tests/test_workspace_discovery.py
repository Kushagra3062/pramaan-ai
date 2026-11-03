"""Database-backed contract tests for authenticated workspace discovery and overview."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.auth import AuthenticatedUser, get_authenticated_user
from app.db.engine import get_db
from app.main import app
from app.models.document import Document
from app.models.enums import DocumentProcessingStatus, RequirementStatus, WorkspaceRole
from app.models.fact_requirement import ExtractedFact, Requirement
from app.models.workspace import Workspace, WorkspaceMember


@pytest.fixture
def api(db_session):
    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        def acting_as(user):
            app.dependency_overrides[get_authenticated_user] = lambda: AuthenticatedUser(
                user=user, subject=user.auth_subject
            )
            return client

        yield acting_as
    app.dependency_overrides.clear()


def _workspace(db, name: str) -> Workspace:
    workspace = Workspace(name=name, slug=f"{name.lower()}-{uuid.uuid4().hex[:8]}")
    db.add(workspace)
    db.flush()
    return workspace


def _document(db, workspace: Workspace, status: str) -> Document:
    document = Document(
        workspace_id=workspace.id,
        original_filename="safe.pdf",
        storage_key="never-returned.pdf",
        sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        mime_type="application/pdf",
        file_size_bytes=1,
        processing_status=status,
    )
    db.add(document)
    db.flush()
    return document


def test_me_and_workspace_list_are_limited_to_current_memberships(api, db_session, test_user):
    visible = _workspace(db_session, "Visible")
    hidden = _workspace(db_session, "Hidden")
    db_session.add(WorkspaceMember(workspace_id=visible.id, user_id=test_user.id, role=WorkspaceRole.EDITOR.value))
    db_session.flush()
    client = api(test_user)
    assert client.get("/api/v1/me").json() == {
        "user_id": str(test_user.id), "email": test_user.email, "display_name": test_user.display_name,
    }
    response = client.get("/api/v1/workspaces")
    assert response.status_code == 200
    assert response.json() == [{"workspace_id": str(visible.id), "name": visible.name, "slug": visible.slug, "role": "EDITOR"}]
    assert str(hidden.id) not in response.text


def test_workspace_overview_counts_only_authorized_workspace(api, db_session, test_user):
    workspace = _workspace(db_session, "Counts")
    other = _workspace(db_session, "Other")
    db_session.add(WorkspaceMember(workspace_id=workspace.id, user_id=test_user.id, role=WorkspaceRole.VIEWER.value))
    ready_document = _document(db_session, workspace, DocumentProcessingStatus.READY.value)
    _document(db_session, workspace, DocumentProcessingStatus.PROCESSING.value)
    _document(db_session, workspace, DocumentProcessingStatus.FAILED.value)
    _document(db_session, other, DocumentProcessingStatus.READY.value)
    db_session.add_all([
        ExtractedFact(workspace_id=workspace.id, document_id=ready_document.id, fact_type="DATE", raw_value="1", source_quote="1", extraction_method="test"),
        Requirement(workspace_id=workspace.id, title="Draft", description="d", requirement_type="OBLIGATION", status=RequirementStatus.DRAFT.value),
        Requirement(workspace_id=workspace.id, title="Active", description="a", requirement_type="OBLIGATION", status=RequirementStatus.ACTIVE.value),
    ])
    db_session.flush()
    client = api(test_user)
    response = client.get(f"/api/v1/workspaces/{workspace.id}/overview")
    assert response.status_code == 200
    assert response.json() == {"workspace_id": str(workspace.id), "total_documents": 3, "processing_documents": 1, "ready_documents": 1, "failed_documents": 1, "extracted_facts": 1, "requirements": 2, "pending_reviews": 1}
    assert client.get(f"/api/v1/workspaces/{other.id}/overview").status_code == 403
