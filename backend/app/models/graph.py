import uuid
from typing import Any

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, FullTimestampMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    GraphEdgeStatus,
    GraphNodeType,
    GraphProvenanceType,
    GraphRelationshipType,
)


class GraphNode(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "graph_nodes"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(255), nullable=False)
    display_label: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("workspace_id", "entity_key", name="uq_graph_nodes_workspace_entity_key"),
        CheckConstraint(
            f"node_type IN ('{GraphNodeType.DOCUMENT.value}', '{GraphNodeType.FACT.value}', '{GraphNodeType.REQUIREMENT.value}', '{GraphNodeType.CONFLICT.value}', '{GraphNodeType.ACTION.value}')",
            name="ck_graph_nodes_type_valid",
        ),
        Index("ix_graph_nodes_workspace_type", "workspace_id", "node_type"),
    )

    # Relationships
    out_edges: Mapped[list["GraphEdge"]] = relationship(
        "GraphEdge",
        foreign_keys="[GraphEdge.source_node_id]",
        back_populates="source_node",
        cascade="all, delete-orphan",
    )
    in_edges: Mapped[list["GraphEdge"]] = relationship(
        "GraphEdge",
        foreign_keys="[GraphEdge.target_node_id]",
        back_populates="target_node",
        cascade="all, delete-orphan",
    )


class GraphEdge(Base, UUIDPrimaryKeyMixin, FullTimestampMixin):
    __tablename__ = "graph_edges"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("graph_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("graph_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    relationship_type: Mapped[str] = mapped_column(String(50), nullable=False)
    provenance_type: Mapped[str] = mapped_column(String(50), nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=GraphEdgeStatus.CANDIDATE.value,
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)

    __table_args__ = (
        CheckConstraint("source_node_id != target_node_id", name="ck_graph_edges_no_self_loop"),
        CheckConstraint(
            f"relationship_type IN ('{GraphRelationshipType.DERIVED_FROM.value}', '{GraphRelationshipType.SUPPORTED_BY.value}', '{GraphRelationshipType.CONTRADICTS.value}', '{GraphRelationshipType.SUPERSEDES.value}', '{GraphRelationshipType.AFFECTS.value}', '{GraphRelationshipType.ASSIGNED_TO.value}')",
            name="ck_graph_edges_rel_type_valid",
        ),
        CheckConstraint(
            f"provenance_type IN ('{GraphProvenanceType.DETERMINISTIC.value}', '{GraphProvenanceType.MODEL_PROPOSED.value}', '{GraphProvenanceType.HUMAN_CONFIRMED.value}')",
            name="ck_graph_edges_provenance_valid",
        ),
        CheckConstraint(
            f"verification_status IN ('{GraphEdgeStatus.CANDIDATE.value}', '{GraphEdgeStatus.VERIFIED.value}', '{GraphEdgeStatus.REJECTED.value}')",
            name="ck_graph_edges_status_valid",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="ck_graph_edges_confidence_bounds",
        ),
        UniqueConstraint(
            "workspace_id",
            "source_node_id",
            "target_node_id",
            "relationship_type",
            "provenance_type",
            name="uq_graph_edges_unique_relation",
        ),
        Index("ix_graph_edges_workspace_source", "workspace_id", "source_node_id"),
        Index("ix_graph_edges_workspace_target", "workspace_id", "target_node_id"),
    )

    # Relationships
    source_node: Mapped["GraphNode"] = relationship(
        "GraphNode", foreign_keys=[source_node_id], back_populates="out_edges"
    )
    target_node: Mapped["GraphNode"] = relationship(
        "GraphNode", foreign_keys=[target_node_id], back_populates="in_edges"
    )
