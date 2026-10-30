import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.repositories.base import BaseRepository
from app.models.enums import FactType, RequirementStatus, VerificationStatus
from app.models.fact_requirement import ExtractedFact, Requirement


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

    def update_status(self, requirement_id: uuid.UUID, status: str) -> Requirement | None:
        req = self.get_by_id(requirement_id)
        if req is not None:
            req.status = status
            self.session.flush()
        return req
