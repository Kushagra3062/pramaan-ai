import uuid
from typing import Any

from sqlalchemy import Row, exists, func, select
from sqlalchemy.orm import Session

from app.db.repositories.base import BaseRepository
from app.models.document import DocumentChunk, DocumentPage
from app.models.enums import FactType, RequirementStatus, VerificationStatus
from app.models.evidence_analysis import EvidenceLink
from app.models.fact_requirement import ExtractedFact, Requirement

PROVENANCE_KEY = "_pramaan"
FINGERPRINT_KEY = "proposal_fingerprint"


class FactRepository(BaseRepository[ExtractedFact]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, ExtractedFact)

    def create(
        self,
        workspace_id: uuid.UUID,
        document_id: uuid.UUID,
        fact_type: str | FactType,
        raw_value: str,
        source_quote: str,
        extraction_method: str = "llm_extraction",
        normalized_value: dict[str, Any] | None = None,
        page_id: uuid.UUID | None = None,
        verification_status: str | VerificationStatus = VerificationStatus.UNVERIFIED.value,
        graph_node_id: uuid.UUID | None = None,
    ) -> ExtractedFact:
        fact = ExtractedFact(
            workspace_id=workspace_id,
            document_id=document_id,
            page_id=page_id,
            fact_type=fact_type.value if isinstance(fact_type, FactType) else fact_type,
            raw_value=raw_value,
            normalized_value=normalized_value,
            source_quote=source_quote,
            extraction_method=extraction_method,
            verification_status=(
                verification_status.value
                if isinstance(verification_status, VerificationStatus)
                else verification_status
            ),
            graph_node_id=graph_node_id,
        )
        return self.add(fact)

    def find_by_fingerprint(
        self, workspace_id: uuid.UUID, document_id: uuid.UUID, fingerprint: str
    ) -> ExtractedFact | None:
        """Oldest fact in this workspace/document carrying the backend proposal fingerprint."""
        stmt = (
            select(ExtractedFact)
            .where(
                ExtractedFact.workspace_id == workspace_id,
                ExtractedFact.document_id == document_id,
                ExtractedFact.normalized_value[PROVENANCE_KEY][FINGERPRINT_KEY].astext == fingerprint,
            )
            .order_by(ExtractedFact.created_at.asc(), ExtractedFact.id.asc())
        )
        return self.session.scalars(stmt).first()

    def list_paginated_for_document(
        self,
        workspace_id: uuid.UUID,
        document_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        fact_type: str | None = None,
        verification_status: str | None = None,
    ) -> tuple[list[tuple[ExtractedFact, int | None]], int]:
        """Workspace/document-scoped page of facts with their page number (outer join).

        Ordered by page number (NULLS LAST), created_at, id. Returns (rows, total) where
        total uses the same filters.
        """
        conditions = [
            ExtractedFact.workspace_id == workspace_id,
            ExtractedFact.document_id == document_id,
        ]
        if fact_type is not None:
            conditions.append(ExtractedFact.fact_type == fact_type)
        if verification_status is not None:
            conditions.append(ExtractedFact.verification_status == verification_status)

        total = self.session.scalar(
            select(func.count()).select_from(ExtractedFact).where(*conditions)
        ) or 0
        stmt = (
            select(ExtractedFact, DocumentPage.page_number)
            .outerjoin(DocumentPage, DocumentPage.id == ExtractedFact.page_id)
            .where(*conditions)
            .order_by(
                DocumentPage.page_number.asc().nulls_last(),
                ExtractedFact.created_at.asc(),
                ExtractedFact.id.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
        rows = [(fact, page_number) for fact, page_number in self.session.execute(stmt).all()]
        return rows, total

    def list_by_document(self, document_id: uuid.UUID) -> list[ExtractedFact]:
        stmt = (
            select(ExtractedFact)
            .where(ExtractedFact.document_id == document_id)
            .order_by(ExtractedFact.created_at.asc())
        )
        return list(self.session.scalars(stmt).all())

    def list_by_workspace(
        self,
        workspace_id: uuid.UUID,
        fact_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ExtractedFact]:
        stmt = select(ExtractedFact).where(ExtractedFact.workspace_id == workspace_id)
        if fact_type:
            stmt = stmt.where(ExtractedFact.fact_type == fact_type)
        stmt = stmt.order_by(ExtractedFact.created_at.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(stmt).all())

    def update_verification(self, fact_id: uuid.UUID, status: str) -> ExtractedFact | None:
        fact = self.get_by_id(fact_id)
        if fact is not None:
            fact.verification_status = status
            self.session.flush()
        return fact


class RequirementRepository(BaseRepository[Requirement]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, Requirement)

    def create(
        self,
        workspace_id: uuid.UUID,
        title: str,
        description: str,
        requirement_type: str,
        normalized_value: dict[str, Any] | None = None,
        status: str | RequirementStatus = RequirementStatus.ACTIVE.value,
        graph_node_id: uuid.UUID | None = None,
    ) -> Requirement:
        req = Requirement(
            workspace_id=workspace_id,
            title=title,
            description=description,
            requirement_type=requirement_type,
            normalized_value=normalized_value,
            status=status.value if isinstance(status, RequirementStatus) else status,
            graph_node_id=graph_node_id,
        )
        return self.add(req)

    def list_by_workspace(
        self,
        workspace_id: uuid.UUID,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Requirement]:
        stmt = select(Requirement).where(Requirement.workspace_id == workspace_id)
        if status:
            stmt = stmt.where(Requirement.status == status)
        stmt = stmt.order_by(Requirement.created_at.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(stmt).all())

    def find_by_fingerprint(self, workspace_id: uuid.UUID, fingerprint: str) -> Requirement | None:
        """Oldest requirement in this workspace carrying the backend proposal fingerprint.

        Requirements have no document_id; the fingerprint itself embeds the document
        and page identity, and document provenance is established via evidence links.
        """
        stmt = (
            select(Requirement)
            .where(
                Requirement.workspace_id == workspace_id,
                Requirement.normalized_value[PROVENANCE_KEY][FINGERPRINT_KEY].astext == fingerprint,
            )
            .order_by(Requirement.created_at.asc(), Requirement.id.asc())
        )
        return self.session.scalars(stmt).first()

    def list_paginated_for_document(
        self,
        workspace_id: uuid.UUID,
        document_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
    ) -> tuple[list[Requirement], int]:
        """Requirements whose evidence links point at chunks of this document.

        requirements has no document_id; provenance is requirement -> evidence_links ->
        document_chunks -> document. EXISTS avoids row duplication from multiple links, so
        pagination and the count stay correct.
        """
        linked = exists().where(
            EvidenceLink.requirement_id == Requirement.id,
            EvidenceLink.workspace_id == workspace_id,
            DocumentChunk.id == EvidenceLink.chunk_id,
            DocumentChunk.workspace_id == workspace_id,
            DocumentChunk.document_id == document_id,
        )
        conditions = [Requirement.workspace_id == workspace_id, linked]
        if status is not None:
            conditions.append(Requirement.status == status)
        total = self.session.scalar(select(func.count()).select_from(Requirement).where(*conditions)) or 0
        stmt = (
            select(Requirement)
            .where(*conditions)
            .order_by(Requirement.created_at.asc(), Requirement.id.asc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt).all()), total

    def update_status(self, requirement_id: uuid.UUID, status: str) -> Requirement | None:
        req = self.get_by_id(requirement_id)
        if req is not None:
            req.status = status
            self.session.flush()
        return req


class EvidenceLinkRepository(BaseRepository[EvidenceLink]):
    """Read-side lookups for evidence links (creation: AnalysisRepository.add_evidence_link)."""

    def __init__(self, session: Session) -> None:
        super().__init__(session, EvidenceLink)

    def find_for_target(
        self,
        workspace_id: uuid.UUID,
        chunk_id: uuid.UUID,
        fact_id: uuid.UUID | None = None,
        requirement_id: uuid.UUID | None = None,
    ) -> EvidenceLink | None:
        if (fact_id is None) == (requirement_id is None):
            raise ValueError("exactly one of fact_id or requirement_id is required")
        stmt = select(EvidenceLink).where(
            EvidenceLink.workspace_id == workspace_id,
            EvidenceLink.chunk_id == chunk_id,
        )
        if fact_id is not None:
            stmt = stmt.where(EvidenceLink.fact_id == fact_id)
        else:
            stmt = stmt.where(EvidenceLink.requirement_id == requirement_id)
        return self.session.scalars(stmt.order_by(EvidenceLink.created_at.asc(), EvidenceLink.id.asc())).first()

    def exists_for_document(self, document_id: uuid.UUID) -> bool:
        """True if any evidence link references any chunk of the document."""
        stmt = (
            select(EvidenceLink.id)
            .join(DocumentChunk, DocumentChunk.id == EvidenceLink.chunk_id)
            .where(DocumentChunk.document_id == document_id)
            .limit(1)
        )
        return self.session.scalars(stmt).first() is not None

    def list_references(
        self,
        workspace_id: uuid.UUID,
        document_id: uuid.UUID,
        *,
        fact_ids: list[uuid.UUID] | None = None,
        requirement_ids: list[uuid.UUID] | None = None,
    ) -> list[Row[Any]]:
        """Evidence references for the given facts OR requirements, restricted to chunks of this
        workspace and document, in one bounded query. Only metadata columns are selected (never
        chunk content or embeddings): evidence_link_id, chunk_id, chunk_index, page_number
        (the chunk's page_start), relationship_type, verification_status, fact_id,
        requirement_id. Ordered by chunk_index, then link id.
        """
        if bool(fact_ids) == bool(requirement_ids):
            raise ValueError("provide exactly one of fact_ids or requirement_ids")
        target = (
            EvidenceLink.fact_id.in_(fact_ids)
            if fact_ids
            else EvidenceLink.requirement_id.in_(requirement_ids)
        )
        stmt = (
            select(
                EvidenceLink.id.label("evidence_link_id"),
                DocumentChunk.id.label("chunk_id"),
                DocumentChunk.chunk_index.label("chunk_index"),
                DocumentChunk.page_start.label("page_number"),
                EvidenceLink.relationship_type.label("relationship_type"),
                EvidenceLink.verification_status.label("verification_status"),
                EvidenceLink.fact_id.label("fact_id"),
                EvidenceLink.requirement_id.label("requirement_id"),
            )
            .join(DocumentChunk, DocumentChunk.id == EvidenceLink.chunk_id)
            .where(
                EvidenceLink.workspace_id == workspace_id,
                DocumentChunk.workspace_id == workspace_id,
                DocumentChunk.document_id == document_id,
                target,
            )
            .order_by(DocumentChunk.chunk_index.asc(), EvidenceLink.id.asc())
        )
        return list(self.session.execute(stmt).all())
