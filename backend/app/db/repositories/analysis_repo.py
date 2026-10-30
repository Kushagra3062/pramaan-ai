import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.repositories.base import BaseRepository
from app.models.enums import (
    AnalysisStatus,
    ClaimSupportStatus,
    EvidenceRelationshipType,
    EvidenceRole,
    VerificationStatus,
)
from app.models.evidence_analysis import Analysis, AnalysisClaim, ClaimEvidence, EvidenceLink


class AnalysisRepository(BaseRepository[Analysis]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Analysis)

    def create(
        self,
        workspace_id: uuid.UUID,
        question: str,
        requested_by: uuid.UUID | None = None,
        model_id: str | None = None,
        prompt_version: str | None = None,
        status: str | AnalysisStatus = AnalysisStatus.CREATED.value,
    ) -> Analysis:
        analysis = Analysis(
            workspace_id=workspace_id,
            requested_by=requested_by,
            question=question,
            status=status.value if isinstance(status, AnalysisStatus) else status,
            model_id=model_id,
            prompt_version=prompt_version,
        )
        return self.add(analysis)

    def list_by_workspace(
        self,
        workspace_id: uuid.UUID,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Analysis]:
        stmt = select(Analysis).where(Analysis.workspace_id == workspace_id)
        if status:
            stmt = stmt.where(Analysis.status == status)
        stmt = stmt.order_by(Analysis.created_at.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(stmt).all())

    def update_status(
        self,
        analysis_id: uuid.UUID,
        status: str | AnalysisStatus,
        result_json: dict[str, Any] | None = None,
    ) -> Analysis | None:
        analysis = self.get_by_id(analysis_id)
        if analysis is not None:
            new_status = status.value if isinstance(status, AnalysisStatus) else status
            analysis.status = new_status
            if result_json is not None:
                analysis.result_json = result_json
            if new_status in (AnalysisStatus.COMPLETED.value, AnalysisStatus.FAILED.value, AnalysisStatus.ABORTED.value):
                analysis.completed_at = datetime.now(UTC)
            self.session.flush()
        return analysis

    def add_claim(
        self,
        analysis_id: uuid.UUID,
        claim_text: str,
        support_status: str | ClaimSupportStatus = ClaimSupportStatus.SUPPORTED.value,
    ) -> AnalysisClaim:
        claim = AnalysisClaim(
            analysis_id=analysis_id,
            claim_text=claim_text,
            support_status=(
                support_status.value
                if isinstance(support_status, ClaimSupportStatus)
                else support_status
            ),
        )
        self.session.add(claim)
        self.session.flush()
        return claim

    def get_claims(self, analysis_id: uuid.UUID) -> list[AnalysisClaim]:
        stmt = select(AnalysisClaim).where(AnalysisClaim.analysis_id == analysis_id)
        return list(self.session.scalars(stmt).all())

    def add_claim_evidence(
        self,
        claim_id: uuid.UUID,
        chunk_id: uuid.UUID,
        evidence_role: str | EvidenceRole = EvidenceRole.PRIMARY.value,
    ) -> ClaimEvidence:
        ev = ClaimEvidence(
            claim_id=claim_id,
            chunk_id=chunk_id,
            evidence_role=(
                evidence_role.value if isinstance(evidence_role, EvidenceRole) else evidence_role
            ),
        )
        self.session.add(ev)
        self.session.flush()
        return ev

    def add_evidence_link(
        self,
        workspace_id: uuid.UUID,
        chunk_id: uuid.UUID,
        fact_id: uuid.UUID | None = None,
        requirement_id: uuid.UUID | None = None,
        relationship_type: str | EvidenceRelationshipType = EvidenceRelationshipType.SUPPORTS.value,
        verification_status: str | VerificationStatus = VerificationStatus.UNVERIFIED.value,
    ) -> EvidenceLink:
        link = EvidenceLink(
            workspace_id=workspace_id,
            chunk_id=chunk_id,
            fact_id=fact_id,
            requirement_id=requirement_id,
            relationship_type=(
                relationship_type.value
                if isinstance(relationship_type, EvidenceRelationshipType)
                else relationship_type
            ),
            verification_status=(
                verification_status.value
                if isinstance(verification_status, VerificationStatus)
                else verification_status
            ),
        )
        self.session.add(link)
        self.session.flush()
        return link

    def list_evidence_links_by_workspace(
        self,
        workspace_id: uuid.UUID,
        limit: int = 200,
        offset: int = 0,
    ) -> list[EvidenceLink]:
        stmt = (
            select(EvidenceLink)
            .where(EvidenceLink.workspace_id == workspace_id)
            .order_by(EvidenceLink.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt).all())
