"""Workspace authorization tests: role matrix, cross-workspace isolation, public endpoints.

These tests deliberately use the *real* ``get_authenticated_user``/``require_workspace_roles``
dependencies and real ``workspace_members`` rows; only the JWT verification step is replaced by
an injected principal (JWKS verification itself is covered offline in ``test_auth.py``).
A valid token alone must never grant access to a workspace the caller does not belong to.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.auth import AuthenticatedUser, get_authenticated_user
from app.api.routes import documents as document_routes
from app.db.engine import get_db
from app.main import app
from app.models.document import Document
from app.models.enums import DocumentProcessingStatus, WorkspaceRole
from app.models.workspace import User, Workspace, WorkspaceMember
from app.services.document_service import ValidationError

READ_ROLES = list(WorkspaceRole)
WRITE_ROLES = [WorkspaceRole.OWNER, WorkspaceRole.ADMIN, WorkspaceRole.EDITOR]
READ_ONLY_ROLES = [WorkspaceRole.REVIEWER, WorkspaceRole.VIEWER]


# ── fixtures ────────────────────────────────────────────────────────────────


def make_workspace(db, name: str) -> Workspace:
    workspace = Workspace(name=name, slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:8]}")
    db.add(workspace)
    db.flush()
    return workspace


def make_user(db, label: str, status: str = "ACTIVE") -> User:
    user = User(
        auth_subject=f"supabase|{label}-{uuid.uuid4().hex[:8]}",
        email=f"{label}-{uuid.uuid4().hex[:6]}@pramaan.ai",
        display_name=label.title(),
        status=status,
    )
    db.add(user)
    db.flush()
    return user


def make_member(db, workspace: Workspace, role: WorkspaceRole, label: str | None = None) -> User:
    user = make_user(db, label or role.value.lower())
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=role.value))
    db.flush()
    return user


def make_document(db, workspace: Workspace, status: str = DocumentProcessingStatus.UPLOADED.value) -> Document:
    document = Document(
        workspace_id=workspace.id,
        original_filename="policy.pdf",
        storage_key=f"{workspace.id}/{uuid.uuid4().hex}.pdf",
        sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        mime_type="application/pdf",
        file_size_bytes=128,
        processing_status=status,
    )
    db.add(document)
    db.flush()
    return document


@pytest.fixture
def workspace(db_session) -> Workspace:
    return make_workspace(db_session, "Alpha Workspace")


@pytest.fixture
def other_workspace(db_session) -> Workspace:
    return make_workspace(db_session, "Beta Workspace")


@pytest.fixture
def members(db_session, workspace) -> dict[WorkspaceRole, User]:
    return {role: make_member(db_session, workspace, role) for role in WorkspaceRole}


@pytest.fixture
def outsider(db_session, other_workspace) -> User:
    """Active user who belongs to another workspace, not this one."""
    return make_member(db_session, other_workspace, WorkspaceRole.OWNER, "outsider")


@pytest.fixture
def document(db_session, workspace) -> Document:
    return make_document(db_session, workspace)


@pytest.fixture
def foreign_document(db_session, other_workspace) -> Document:
    return make_document(db_session, other_workspace)


@pytest.fixture
def anon_api(db_session):
    """TestClient with the real auth dependencies and no credentials supplied."""

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def api(db_session):
    """Factory returning a TestClient acting as a given local user (real membership checks)."""

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as client:

        def acting_as(user: User) -> TestClient:
            app.dependency_overrides[get_authenticated_user] = lambda: AuthenticatedUser(
                user=user, subject=user.auth_subject
            )
            return client

        yield acting_as
    app.dependency_overrides.clear()


def workspace_url(workspace: Workspace) -> str:
    return f"/api/v1/workspaces/{workspace.id}/documents"


# ── authentication is required everywhere under /api/v1/workspaces ──────────


def protected_routes() -> list[tuple[str, str]]:
    routes: list[tuple[str, str]] = []
    for path, methods in app.openapi()["paths"].items():
        if not path.startswith("/api/v1/workspaces/"):
            continue
        for method in methods:
            if method.lower() in {"get", "post", "put", "patch", "delete"}:
                routes.append((method.lower(), path))
    return routes


def test_the_route_inventory_is_not_empty():
    assert len(protected_routes()) >= 8


@pytest.mark.parametrize(("method", "path"), protected_routes())
def test_every_workspace_route_requires_authentication(anon_api, method, path):
    """A route that loses its dependency on authentication fails this test."""
    url = path.replace("{workspace_id}", str(uuid.uuid4())).replace("{document_id}", str(uuid.uuid4()))
    response = anon_api.request(method, url, json={})
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_root_and_liveness_are_public(anon_api):
    assert anon_api.get("/").status_code == 200
    assert anon_api.get("/health/live").json() == {"status": "ok", "check": "live"}
    ready = anon_api.get("/health/ready")
    assert ready.status_code == 200 and ready.json()["database"] == "ok"


# ── read permission: every role may read ────────────────────────────────────


@pytest.mark.parametrize("role", READ_ROLES)
def test_every_role_can_read_documents(api, members, workspace, document, role):
    client = api(members[role])
    assert client.get(workspace_url(workspace)).status_code == 200
    assert client.get(f"{workspace_url(workspace)}/{document.id}").status_code == 200
    assert client.get(f"{workspace_url(workspace)}/{document.id}/pages").status_code == 200
    assert client.get(f"{workspace_url(workspace)}/{document.id}/chunks").status_code == 200


@pytest.mark.parametrize("role", READ_ROLES)
def test_every_role_can_read_facts_and_requirements(api, members, workspace, document, role):
    client = api(members[role])
    facts = client.get(f"{workspace_url(workspace)}/{document.id}/facts")
    requirements = client.get(f"{workspace_url(workspace)}/{document.id}/requirements")
    assert facts.status_code == 200 and facts.json()["total"] == 0
    assert requirements.status_code == 200 and requirements.json()["total"] == 0


# ── write permission: OWNER / ADMIN / EDITOR only ───────────────────────────


@pytest.mark.parametrize("role", WRITE_ROLES)
def test_writers_may_reach_upload(api, members, workspace, role):
    response = api(members[role]).post(
        workspace_url(workspace), files={"file": ("policy.pdf", b"not really a pdf", "application/pdf")}
    )
    assert response.status_code == 422  # passed authorization, failed content validation


@pytest.mark.parametrize("role", READ_ONLY_ROLES)
def test_read_only_roles_cannot_upload(api, members, workspace, role):
    response = api(members[role]).post(
        workspace_url(workspace), files={"file": ("policy.pdf", b"%PDF-1.4", "application/pdf")}
    )
    assert response.status_code == 403
    assert "denied" in response.json()["detail"].lower()


@pytest.mark.parametrize("role", WRITE_ROLES)
def test_writers_may_reach_ocr(api, members, workspace, document, monkeypatch, role):
    calls: list[str] = []

    def reject(**kwargs):
        calls.append("called")
        raise ValidationError("no pages selected")

    monkeypatch.setattr(document_routes, "process_document_ocr", reject)
    response = api(members[role]).post(f"{workspace_url(workspace)}/{document.id}/ocr", json={"page_numbers": [1]})
    assert response.status_code == 422 and calls == ["called"]


@pytest.mark.parametrize("role", READ_ONLY_ROLES)
def test_read_only_roles_cannot_trigger_ocr(api, members, workspace, document, monkeypatch, role):
    calls: list[str] = []

    def reject(**kwargs):
        calls.append("called")
        raise ValidationError("no pages selected")

    monkeypatch.setattr(document_routes, "process_document_ocr", reject)
    response = api(members[role]).post(f"{workspace_url(workspace)}/{document.id}/ocr", json={"page_numbers": [1]})
    assert response.status_code == 403 and calls == []


@pytest.mark.parametrize("role", WRITE_ROLES)
def test_writers_may_reach_canonical_selection(api, members, workspace, document, monkeypatch, role):
    def reject(**kwargs):
        raise ValidationError("bad selection")

    monkeypatch.setattr(document_routes, "select_canonical_extraction", reject)
    response = api(members[role]).put(
        f"{workspace_url(workspace)}/{document.id}/canonical", json={"provider": "tesseract"}
    )
    assert response.status_code == 422


@pytest.mark.parametrize("role", READ_ONLY_ROLES)
def test_read_only_roles_cannot_change_canonical_text(api, members, workspace, document, monkeypatch, role):
    def reject(**kwargs):
        raise AssertionError("canonical selection must not be reached for a read-only role")

    monkeypatch.setattr(document_routes, "select_canonical_extraction", reject)
    url = f"{workspace_url(workspace)}/{document.id}/canonical"
    assert api(members[role]).put(url, json={"provider": "tesseract"}).status_code == 403


@pytest.mark.parametrize("role", WRITE_ROLES)
def test_writers_may_reach_extraction(api, members, workspace, document, role):
    # Document is UPLOADED, so authorization passes and the readiness gate answers 409.
    response = api(members[role]).post(f"{workspace_url(workspace)}/{document.id}/extract")
    assert response.status_code == 409
    assert "READY" in response.json()["detail"]


@pytest.mark.parametrize("role", READ_ONLY_ROLES)
def test_read_only_roles_cannot_spend_model_calls(api, members, workspace, document, monkeypatch, role):
    def boom(*args, **kwargs):
        raise AssertionError("extraction must not run for a read-only role")

    monkeypatch.setattr("app.api.routes.extraction.run_document_extraction", boom)
    response = api(members[role]).post(f"{workspace_url(workspace)}/{document.id}/extract")
    assert response.status_code == 403


# ── non-members and cross-workspace isolation ───────────────────────────────


def test_non_member_is_denied_read_and_write(api, outsider, workspace, document):
    client = api(outsider)
    assert client.get(workspace_url(workspace)).status_code == 403
    assert client.get(f"{workspace_url(workspace)}/{document.id}").status_code == 403
    assert client.get(f"{workspace_url(workspace)}/{document.id}/facts").status_code == 403
    assert client.post(f"{workspace_url(workspace)}/{document.id}/extract").status_code == 403
    assert client.post(f"{workspace_url(workspace)}/{document.id}/ocr", json={}).status_code == 403


def test_valid_token_does_not_grant_another_workspace(api, members, workspace, other_workspace, foreign_document):
    """Membership is the authority: an OWNER of one workspace gets 403 on another."""
    client = api(members[WorkspaceRole.OWNER])
    assert client.get(workspace_url(workspace)).status_code == 200
    assert client.get(workspace_url(other_workspace)).status_code == 403
    assert client.get(f"{workspace_url(other_workspace)}/{foreign_document.id}").status_code == 403
    assert client.get(f"{workspace_url(other_workspace)}/{foreign_document.id}/facts").status_code == 403


def test_member_of_both_workspaces_can_read_both(api, db_session, members, workspace, other_workspace):
    user = members[WorkspaceRole.ADMIN]
    db_session.add(WorkspaceMember(workspace_id=other_workspace.id, user_id=user.id, role=WorkspaceRole.VIEWER.value))
    db_session.flush()
    client = api(user)
    assert client.get(workspace_url(workspace)).status_code == 200
    assert client.get(workspace_url(other_workspace)).status_code == 200


def test_cross_workspace_document_id_is_404_not_403(api, members, workspace, foreign_document):
    """Inside a workspace the caller belongs to, a foreign document is simply not found."""
    client = api(members[WorkspaceRole.OWNER])
    assert client.get(f"{workspace_url(workspace)}/{foreign_document.id}").status_code == 404
    assert client.get(f"{workspace_url(workspace)}/{foreign_document.id}/facts").status_code == 404
    assert client.post(f"{workspace_url(workspace)}/{foreign_document.id}/extract").status_code == 404


def test_deleted_or_deleting_document_is_404_for_members(api, db_session, members, workspace, document):
    client = api(members[WorkspaceRole.OWNER])
    document.processing_status = DocumentProcessingStatus.DELETING.value
    db_session.flush()
    assert client.get(f"{workspace_url(workspace)}/{document.id}/facts").status_code == 404
    document.processing_status = DocumentProcessingStatus.READY.value
    document.deleted_at = datetime.now(UTC)
    db_session.flush()
    assert client.get(f"{workspace_url(workspace)}/{document.id}/facts").status_code == 404


# ── the database, not the token, is the authority ───────────────────────────


def test_role_change_takes_effect_immediately(api, db_session, members, workspace, document):
    user = members[WorkspaceRole.EDITOR]
    client = api(user)
    assert client.post(f"{workspace_url(workspace)}/{document.id}/extract").status_code == 409  # EDITOR

    membership = db_session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id, WorkspaceMember.user_id == user.id
        )
    )
    membership.role = WorkspaceRole.VIEWER.value
    db_session.flush()

    assert client.post(f"{workspace_url(workspace)}/{document.id}/extract").status_code == 403
    assert client.get(workspace_url(workspace)).status_code == 200  # reads still allowed


def test_workspace_that_does_not_exist_is_403_for_non_member(api, members, db_session):
    client = api(members[WorkspaceRole.OWNER])
    assert client.get(f"/api/v1/workspaces/{uuid.uuid4()}/documents").status_code == 403
