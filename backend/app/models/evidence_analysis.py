import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    AnalysisStatus,
    ClaimSupportStatus,
    EvidenceRelationshipType,
    EvidenceRole,
    VerificationStatus,
)

if TYPE_CHECKING:
    from app.models.document import DocumentChunk
    from app.models.fact_requirement import ExtractedFact, Requirement
    from app.models.workspace import User, Workspace


class EvidenceLink(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "evidence_links"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_chunks.id", ondelete="CASCADE"),
        nullable=False,
    )
    fact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("extracted_facts.id", ondelete="CASCADE"),
        nullable=True,
    )
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("requirements.id", ondelete="CASCADE"),
        nullable=True,
    )
    relationship_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=EvidenceRelationshipType.SUPPORTS.value,
    )
    verification_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=VerificationStatus.UNVERIFIED.value,
    )

    __table_args__ = (
        CheckConstraint(
            "((fact_id IS NOT NULL AND requirement_id IS NULL) OR (fact_id IS NULL AND requirement_id IS NOT NULL))",
            name="ck_evidence_links_target_valid",
        ),
        CheckConstraint(
            f"relationship_type IN ('{EvidenceRelationshipType.SUPPORTS.value}', '{EvidenceRelationshipType.REFUTES.value}', '{EvidenceRelationshipType.CONTEXT.value}')",
            name="ck_evidence_links_rel_type_valid",
        ),
        CheckConstraint(
            f"verification_status IN ('{VerificationStatus.UNVERIFIED.value}', '{VerificationStatus.VERIFIED.value}', '{VerificationStatus.REJECTED.value}')",
            name="ck_evidence_links_status_valid",
        ),
        Index("ix_evidence_links_workspace_chunk", "workspace_id", "chunk_id"),
        Index("ix_evidence_links_workspace_fact", "workspace_id", "fact_id"),
        Index("ix_evidence_links_workspace_req", "workspace_id", "requirement_id"),
    )

    # Relationships
    chunk: Mapped["DocumentChunk"] = relationship("DocumentChunk")
    fact: Mapped["ExtractedFact | None"] = relationship("ExtractedFact")
    requirement: Mapped["Requirement | None"] = relationship("Requirement")


class Analysis(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "analyses"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=AnalysisStatus.CREATED.value,
    )
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            f"status IN ('{AnalysisStatus.CREATED.value}', '{AnalysisStatus.AUTHORIZED.value}', '{AnalysisStatus.RETRIEVING.value}', '{AnalysisStatus.REASONING.value}', '{AnalysisStatus.VERIFYING.value}', '{AnalysisStatus.COMPLETED.value}', '{AnalysisStatus.FAILED.value}', '{AnalysisStatus.ABORTED.value}')",
            name="ck_analyses_status_valid",
        ),
        Index("ix_analyses_workspace_created", "workspace_id", text("created_at DESC")),
    )

    # Relationships
    workspace: Mapped["Workspace"] = relationship("Workspace")
    requester: Mapped["User | None"] = relationship("User")
    claims: Mapped[list["AnalysisClaim"]] = relationship(
        "AnalysisClaim", back_populates="analysis", cascade="all, delete-orphan"
    )


class AnalysisClaim(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "analysis_claims"

    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analyses.id", ondelete="CASCADE"),
        nullable=False,
    )
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    support_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=ClaimSupportStatus.SUPPORTED.value,
    )

    __table_args__ = (
        CheckConstraint(
            f"support_status IN ('{ClaimSupportStatus.SUPPORTED.value}', '{ClaimSupportStatus.PARTIALLY_SUPPORTED.value}', '{ClaimSupportStatus.CONTRADICTED.value}', '{ClaimSupportStatus.UNSUPPORTED.value}')",
            name="ck_analysis_claims_support_status_valid",
        ),
        Index("ix_analysis_claims_analysis_id", "analysis_id"),
    )

    # Relationships
    analysis: Mapped["Analysis"] = relationship("Analysis", back_populates="claims")
    evidence: Mapped[list["ClaimEvidence"]] = relationship(
        "ClaimEvidence", back_populates="claim", cascade="all, delete-orphan"
    )


class ClaimEvidence(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "claim_evidence"

    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analysis_claims.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_chunks.id", ondelete="CASCADE"),
        nullable=False,
    )
    evidence_role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=EvidenceRole.PRIMARY.value,
    )

    __table_args__ = (
        CheckConstraint(
            f"evidence_role IN ('{EvidenceRole.PRIMARY.value}', '{EvidenceRole.SUPPORTING.value}', '{EvidenceRole.CONTRADICTING.value}')",
            name="ck_claim_evidence_role_valid",
        ),
        UniqueConstraint("claim_id", "chunk_id", "evidence_role", name="uq_claim_evidence_unique"),
        Index("ix_claim_evidence_claim_id", "claim_id"),
        Index("ix_claim_evidence_chunk_id", "chunk_id"),
    )

    # Relationships
    claim: Mapped["AnalysisClaim"] = relationship("AnalysisClaim", back_populates="evidence")
    chunk: Mapped["DocumentChunk"] = relationship("DocumentChunk")
