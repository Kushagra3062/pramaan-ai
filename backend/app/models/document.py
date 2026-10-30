import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, FullTimestampMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import DocumentProcessingStatus

if TYPE_CHECKING:
    from app.models.fact_requirement import ExtractedFact
    from app.models.workspace import User, Workspace


class Document(Base, UUIDPrimaryKeyMixin, FullTimestampMixin):
    __tablename__ = "documents"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detected_language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    processing_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=DocumentProcessingStatus.UPLOADED.value,
    )
    error_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    graph_node_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("graph_nodes.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "sha256", name="uq_documents_workspace_sha256"),
        CheckConstraint(
            f"processing_status IN ('{DocumentProcessingStatus.UPLOADED.value}', '{DocumentProcessingStatus.VALIDATING.value}', '{DocumentProcessingStatus.PROCESSING.value}', '{DocumentProcessingStatus.READY.value}', '{DocumentProcessingStatus.FAILED.value}', '{DocumentProcessingStatus.DELETING.value}', '{DocumentProcessingStatus.DELETED.value}')",
            name="ck_documents_processing_status_valid",
        ),
        Index("ix_documents_workspace_created", "workspace_id", text("created_at DESC")),
        Index("ix_documents_workspace_status", "workspace_id", "processing_status"),
    )

    # Relationships
    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="documents")
    uploader: Mapped["User | None"] = relationship("User")
    pages: Mapped[list["DocumentPage"]] = relationship(
        "DocumentPage", back_populates="document", cascade="all, delete-orphan", order_by="DocumentPage.page_number"
    )
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        "DocumentChunk", back_populates="document", cascade="all, delete-orphan", order_by="DocumentChunk.chunk_index"
    )
    facts: Mapped[list["ExtractedFact"]] = relationship(
        "ExtractedFact", back_populates="document", cascade="all, delete-orphan"
    )


class DocumentPage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "document_pages"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(50), nullable=False)
    extraction_status: Mapped[str] = mapped_column(String(50), nullable=False, default="COMPLETED")
    page_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("document_id", "page_number", name="uq_document_pages_page_num"),
        CheckConstraint("page_number >= 1", name="ck_document_pages_page_num_positive"),
        Index("ix_document_pages_document_page", "document_id", "page_number"),
    )

    # Relationships
    document: Mapped["Document"] = relationship("Document", back_populates="pages")


class DocumentChunk(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "document_chunks"

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
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_version: Mapped[str] = mapped_column(String(50), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_doc_chunk_idx"),
        CheckConstraint("page_start >= 1", name="ck_document_chunks_page_start_positive"),
        CheckConstraint("page_end >= page_start", name="ck_document_chunks_page_range_valid"),
        CheckConstraint("chunk_index >= 0", name="ck_document_chunks_chunk_index_non_negative"),
        Index("ix_document_chunks_workspace_doc", "workspace_id", "document_id"),
        Index("ix_document_chunks_fts", text("to_tsvector('simple', content)"), postgresql_using="gin"),
        Index(
            "ix_document_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    # Relationships
    document: Mapped["Document"] = relationship("Document", back_populates="chunks")
