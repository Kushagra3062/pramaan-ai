"""Database-backed tests for conflict detection and action transitions."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.enums import ActionStatus, ConflictStatus
from app.models.fact_requirement import ExtractedFact
from app.models.workspace import User, Workspace
from app.services.action_service import (
    ActionTransitionError,
    approve_action,
    create_action,
    reject_action,
)
from app.services.conflict_service import detect_conflict_candidates


def _document(db_session: Session, workspace_id, name: str) -> Document:
    doc = Document(
        workspace_id=workspace_id,
        original_filename=name,
        storage_key=f"test/{uuid.uuid4().hex}",
        sha256=uuid.uuid4().hex * 2,
        mime_type="application/pdf",
        file_size_bytes=10,
        processing_status="READY",
    )
    db_session.add(doc)
    db_session.flush()
    return doc


def _fact(db_session: Session, workspace_id, document_id, fact_type: str, raw_value: str) -> ExtractedFact:
    fact = ExtractedFact(
        workspace_id=workspace_id,
        document_id=document_id,
        fact_type=fact_type,
        raw_value=raw_value,
        source_quote=raw_value,
        extraction_method="test",
    )
    db_session.add(fact)
    db_session.flush()
    return fact


def test_detection_creates_open_candidates_for_cross_document_mismatch(
    db_session: Session, test_workspace: Workspace
):
    first = _document(db_session, test_workspace.id, "a.pdf")
    second = _document(db_session, test_workspace.id, "b.pdf")
    _fact(db_session, test_workspace.id, first.id, "DEADLINE", "within 30 days")
    _fact(db_session, test_workspace.id, second.id, "DEADLINE", "within 45 days")
    summary, created = detect_conflict_candidates(db_session, test_workspace.id)
    assert summary.candidates_created == 1
    assert created[0].status == ConflictStatus.OPEN.value
    assert created[0].fact_a_id != created[0].fact_b_id
    # Second run reuses the existing pair instead of duplicating it.
    rerun, _ = detect_conflict_candidates(db_session, test_workspace.id)
    assert rerun.candidates_created == 0
    assert rerun.candidates_skipped_existing == 1


def test_detection_ignores_same_value_and_same_document(db_session: Session, test_workspace: Workspace):
    first = _document(db_session, test_workspace.id, "a.pdf")
    second = _document(db_session, test_workspace.id, "b.pdf")
    _fact(db_session, test_workspace.id, first.id, "AMOUNT", "INR 5000")
    _fact(db_session, test_workspace.id, second.id, "AMOUNT", "INR 5000")
    _fact(db_session, test_workspace.id, first.id, "AMOUNT", "INR 9000")
    summary, _ = detect_conflict_candidates(db_session, test_workspace.id)
    assert summary.candidates_created == 0


def test_action_create_is_idempotent_on_key(db_session: Session, test_workspace: Workspace, test_user: User):
    first = create_action(db_session, test_workspace.id, "Notify finance", idempotency_key="demo-1")
    second = create_action(db_session, test_workspace.id, "Notify finance", idempotency_key="demo-1")
    assert first.created is True
    assert second.created is False
    assert first.action.id == second.action.id
    assert first.action.status == ActionStatus.PROPOSED.value


def test_approve_and_reject_require_mutable_state(db_session: Session, test_workspace: Workspace, test_user: User):
    outcome = create_action(db_session, test_workspace.id, "Escalate conflict")
    approved = approve_action(db_session, outcome.action, approved_by=test_user.id, rationale="Verified")
    assert approved.status == ActionStatus.APPROVED.value
    assert approved.approved_by == test_user.id
    with pytest.raises(ActionTransitionError):
        reject_action(db_session, approved, rejected_by=test_user.id)
    with pytest.raises(ActionTransitionError):
        approve_action(db_session, approved, approved_by=test_user.id)
