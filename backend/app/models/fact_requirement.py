import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, FullTimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import FactType, RequirementStatus, VerificationStatus

if TYPE_CHECKING:
    from app.models.document import Document, DocumentPage
    from app.models.graph import GraphNode
    from app.models.workspace import Workspace


class ExtractedFact(Base, UUIDPrimaryKeyMixin, FullTimestampMixin):
    __tablename__ = "extracted_facts"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    page_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_pages.id", ondelete="SET NULL"),
        nullable=True,
    )
    fact_type: Mapped[str] = mapped_column(String(50), nullable=False)
    raw_value: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    source_quote: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(50), nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=VerificationStatus.UNVERIFIED.value,
    )
    graph_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("graph_nodes.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )

    __table_args__ = (
        CheckConstraint(
            f"fact_type IN ('{FactType.DATE.value}', '{FactType.AMOUNT.value}', '{FactType.CURRENCY.value}', '{FactType.OBLIGATION.value}', '{FactType.DEADLINE.value}', '{FactType.APPROVAL_CONDITION.value}', '{FactType.IDENTIFIER.value}', '{FactType.RESPONSIBLE_DEPARTMENT.value}')",
            name="ck_extracted_facts_type_valid",
        ),
        CheckConstraint(
            f"verification_status IN ('{VerificationStatus.UNVERIFIED.value}', '{VerificationStatus.VERIFIED.value}', '{VerificationStatus.REJECTED.value}')",
            name="ck_extracted_facts_verification_status_valid",
        ),
        Index("ix_extracted_facts_workspace_doc", "workspace_id", "document_id"),
        Index("ix_extracted_facts_workspace_type", "workspace_id", "fact_type"),
    )

    # Relationships
    workspace: Mapped["Workspace"] = relationship("Workspace")
    document: Mapped["Document"] = relationship("Document", back_populates="facts")
    page: Mapped["DocumentPage | None"] = relationship("DocumentPage")
    graph_node: Mapped["GraphNode | None"] = relationship("GraphNode")


class Requirement(Base, UUIDPrimaryKeyMixin, FullTimestampMixin):
    __tablename__ = "requirements"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    requirement_type: Mapped[str] = mapped_column(String(50), nullable=False)
    normalized_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=RequirementStatus.ACTIVE.value,
    )
    graph_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("graph_nodes.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )

    __table_args__ = (
        CheckConstraint(
            f"status IN ('{RequirementStatus.ACTIVE.value}', '{RequirementStatus.SUPERSEDED.value}', '{RequirementStatus.INACTIVE.value}', '{RequirementStatus.DRAFT.value}')",
            name="ck_requirements_status_valid",
        ),
        Index("ix_requirements_workspace_status", "workspace_id", "status"),
    )

    # Relationships
    workspace: Mapped["Workspace"] = relationship("Workspace")
    graph_node: Mapped["GraphNode | None"] = relationship("GraphNode")
