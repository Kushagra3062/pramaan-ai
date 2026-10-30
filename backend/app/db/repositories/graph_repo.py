import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.repositories.base import BaseRepository
from app.models.enums import (
    GraphEdgeStatus,
    GraphNodeType,
    GraphProvenanceType,
    GraphRelationshipType,
)
from app.models.graph import GraphEdge, GraphNode


class GraphRepository(BaseRepository[GraphNode]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, GraphNode)

    def create_node(
        self,
        workspace_id: uuid.UUID,
        node_type: str | GraphNodeType,
        entity_key: str,
        display_label: str,
        metadata: dict[str, Any] | None = None,
    ) -> GraphNode:
        node = GraphNode(
            workspace_id=workspace_id,
            node_type=node_type.value if isinstance(node_type, GraphNodeType) else node_type,
            entity_key=entity_key,
            display_label=display_label,
            metadata_=metadata or {},
        )
        return self.add(node)

    def get_node_by_entity_key(self, workspace_id: uuid.UUID, entity_key: str) -> GraphNode | None:
        stmt = select(GraphNode).where(
            GraphNode.workspace_id == workspace_id,
            GraphNode.entity_key == entity_key,
        )
        return self.session.scalars(stmt).first()

    def list_nodes_by_workspace(
        self,
        workspace_id: uuid.UUID,
        node_type: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[GraphNode]:
        stmt = select(GraphNode).where(GraphNode.workspace_id == workspace_id)
        if node_type:
            stmt = stmt.where(GraphNode.node_type == node_type)
        stmt = stmt.order_by(GraphNode.created_at.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(stmt).all())

    def create_edge(
        self,
        workspace_id: uuid.UUID,
        source_node_id: uuid.UUID,
        target_node_id: uuid.UUID,
        relationship_type: str | GraphRelationshipType,
        provenance_type: str | GraphProvenanceType,
        verification_status: str | GraphEdgeStatus = GraphEdgeStatus.CANDIDATE.value,
        confidence: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GraphEdge:
        edge = GraphEdge(
            workspace_id=workspace_id,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            relationship_type=(
                relationship_type.value
                if isinstance(relationship_type, GraphRelationshipType)
                else relationship_type
            ),
            provenance_type=(
                provenance_type.value
                if isinstance(provenance_type, GraphProvenanceType)
                else provenance_type
            ),
            verification_status=(
                verification_status.value
                if isinstance(verification_status, GraphEdgeStatus)
                else verification_status
            ),
            confidence=confidence,
            metadata_=metadata or {},
        )
        self.session.add(edge)
        self.session.flush()
        return edge

    def get_edge_by_id(self, edge_id: uuid.UUID) -> GraphEdge | None:
        return self.session.get(GraphEdge, edge_id)

    def get_out_edges(
        self,
        node_id: uuid.UUID,
        relationship_type: str | None = None,
    ) -> list[GraphEdge]:
        stmt = select(GraphEdge).where(GraphEdge.source_node_id == node_id)
        if relationship_type:
            stmt = stmt.where(GraphEdge.relationship_type == relationship_type)
        return list(self.session.scalars(stmt).all())

    def get_in_edges(
        self,
        node_id: uuid.UUID,
        relationship_type: str | None = None,
    ) -> list[GraphEdge]:
        stmt = select(GraphEdge).where(GraphEdge.target_node_id == node_id)
        if relationship_type:
            stmt = stmt.where(GraphEdge.relationship_type == relationship_type)
        return list(self.session.scalars(stmt).all())

    def list_edges_by_workspace(
        self,
        workspace_id: uuid.UUID,
        relationship_type: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[GraphEdge]:
        stmt = select(GraphEdge).where(GraphEdge.workspace_id == workspace_id)
        if relationship_type:
            stmt = stmt.where(GraphEdge.relationship_type == relationship_type)
        stmt = stmt.order_by(GraphEdge.created_at.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(stmt).all())
