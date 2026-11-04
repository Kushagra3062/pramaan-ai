"""Investigate API: ask grounded questions, read Decision Packets (FR-010/FR-015).

POST /investigate spends at most one budgeted model call; retrieval misses
abstain without any model call. GET endpoints serve the Decision Packet view:
answer status, claim ledger with per-claim evidence, unknowns, and drafts.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth import (
    AuthenticatedUser,
    get_authenticated_user,
    require_workspace_read,
    require_workspace_write,
)
from app.core.config import Settings, get_settings
from app.db.engine import get_db
from app.db.repositories.analysis_repo import AnalysisRepository
from app.models.document import Document, DocumentChunk
from app.models.enums import AnalysisStatus
from app.models.evidence_analysis import Analysis, ClaimEvidence
from app.schemas.analysis_api import (
    AnalysisClaimResponse,
    AnalysisDetailResponse,
    AnalysisSummaryResponse,
    ClaimEvidenceResponse,
    InvestigateRequest,
    PaginatedAnalysesResponse,
)
from app.services.analysis_service import AnalysisInputError, AnalysisModelError, run_investigation

router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/analyses",
    tags=["analyses"],
    dependencies=[Depends(require_workspace_read)],
)

_VALID_STATUSES = {item.value for item in AnalysisStatus}
_EXCERPT_CHARS = 400


def _summary(analysis: Analysis) -> AnalysisSummaryResponse:
    return AnalysisSummaryResponse(
        id=analysis.id, workspace_id=analysis.workspace_id, question=analysis.question,
        status=analysis.status, model_id=analysis.model_id, prompt_version=analysis.prompt_version,
        created_at=analysis.created_at, completed_at=analysis.completed_at,
    )


def _claim_evidence(session: Session, link: ClaimEvidence) -> ClaimEvidenceResponse | None:
    chunk = session.get(DocumentChunk, link.chunk_id)
    if chunk is None or chunk.workspace_id is None:
        return None
    document = session.get(Document, chunk.document_id)
    excerpt = chunk.content[:_EXCERPT_CHARS]
    if len(chunk.content) > _EXCERPT_CHARS:
        excerpt += "…"
    return ClaimEvidenceResponse(
        chunk_id=chunk.id, document_id=chunk.document_id,
        document_filename=document.original_filename if document else "Unknown document",
        page_start=chunk.page_start, page_end=chunk.page_end,
        excerpt=excerpt, evidence_role=link.evidence_role,
    )


def _detail(session: Session, analysis: Analysis) -> AnalysisDetailResponse:
    repo = AnalysisRepository(session)
    claims = []
    for claim in repo.get_claims(analysis.id):
        evidence = []
        for link in claim.evidence:
            item = _claim_evidence(session, link)
            if item is not None:
                evidence.append(item)
        claims.append(AnalysisClaimResponse(
            id=claim.id, claim_text=claim.claim_text,
            support_status=claim.support_status, evidence=evidence,
        ))
    return AnalysisDetailResponse(
        id=analysis.id, workspace_id=analysis.workspace_id, question=analysis.question,
        status=analysis.status, model_id=analysis.model_id, prompt_version=analysis.prompt_version,
        result=dict(analysis.result_json or {}) if analysis.result_json else None,
        claims=claims, created_at=analysis.created_at, completed_at=analysis.completed_at,
    )


def _get_scoped_analysis(session: Session, workspace_id: uuid.UUID, analysis_id: uuid.UUID) -> Analysis:
    analysis = AnalysisRepository(session).get_by_id(analysis_id)
    if analysis is None or analysis.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Analysis not found.")
    return analysis


@router.get("", response_model=PaginatedAnalysesResponse)
def list_analyses(
    workspace_id: uuid.UUID,
    session: Session = Depends(get_db),
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PaginatedAnalysesResponse:
    if status_filter and status_filter not in _VALID_STATUSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown analysis status.")
    items = AnalysisRepository(session).list_by_workspace(workspace_id, status=status_filter, limit=limit, offset=offset)
    count_stmt = select(func.count(Analysis.id)).where(Analysis.workspace_id == workspace_id)
    if status_filter:
        count_stmt = count_stmt.where(Analysis.status == status_filter)
    total = session.scalar(count_stmt) or 0
    return PaginatedAnalysesResponse(items=[_summary(item) for item in items], total=total, limit=limit, offset=offset)


@router.get("/{analysis_id}", response_model=AnalysisDetailResponse)
def get_analysis(
    workspace_id: uuid.UUID,
    analysis_id: uuid.UUID,
    session: Session = Depends(get_db),
) -> AnalysisDetailResponse:
    return _detail(session, _get_scoped_analysis(session, workspace_id, analysis_id))


@router.post("/investigate", response_model=AnalysisDetailResponse, dependencies=[Depends(require_workspace_write)])
def investigate(
    workspace_id: uuid.UUID,
    body: InvestigateRequest,
    request: Request,
    session: Session = Depends(get_db),
    principal: AuthenticatedUser = Depends(get_authenticated_user),
    settings: Settings = Depends(get_settings),
) -> AnalysisDetailResponse:
    try:
        analysis = run_investigation(
            session, workspace_id, body.question.strip(),
            document_ids=list(body.document_ids),
            requested_by=principal.user.id,
            request_id=request.headers.get("X-Request-ID"),
            settings=settings,
        )
    except AnalysisInputError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except AnalysisModelError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    session.commit()
    session.refresh(analysis)
    return _detail(session, analysis)
