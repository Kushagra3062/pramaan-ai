"""Evidence-to-Impact Graph read API: nodes, edges, bounded traversal (FR-031/FR-032)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth import require_workspace_read
from app.db.engine import get_db
from app.db.repositories.graph_repo import GraphRepository
from app.models.enums import GraphNodeType, GraphRelationshipType
from app.models.graph import GraphEdge, GraphNode
from app.schemas.graph_api import (
    GraphEdgeResponse,
    GraphNodeResponse,
    ImpactPathResponse,
    ImpactResponse,
    PaginatedGraphEdgesResponse,
    PaginatedGraphNodesResponse,
)
from app.services.impact_service import MAX_DEPTH, is_validated_edge, traverse_impact

router = APIRouter(
    prefix="/api/v1/workspaces/{workspace_id}/graph",
    tags=["graph"],
    dependencies=[Depends(require_workspace_read)],
)

_VALID_NODE_TYPES = {item.value for item in GraphNodeType}
_VALID_RELATIONSHIPS = {item.value for item in GraphRelationshipType}


def _node_to_response(node: GraphNode) -> GraphNodeResponse:
    return GraphNodeResponse(
        id=node.id, node_type=node.node_type, entity_key=node.entity_key,
        display_label=node.display_label, metadata=dict(node.metadata_ or {}), created_at=node.created_at,
    )


def _edge_to_response(edge: GraphEdge) -> GraphEdgeResponse:
    return GraphEdgeResponse(
        id=edge.id, source_node_id=edge.source_node_id, target_node_id=edge.target_node_id,
        relationship_type=edge.relationship_type, provenance_type=edge.provenance_type,
        verification_status=edge.verification_status, confidence=edge.confidence,
        validated=is_validated_edge(edge), metadata=dict(edge.metadata_ or {}),
    )


@router.get("/nodes", response_model=PaginatedGraphNodesResponse)
def list_nodes(
    workspace_id: uuid.UUID,
    session: Session = Depends(get_db),
    node_type: str | None = Query(default=None, max_length=30),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedGraphNodesResponse:
    if node_type and node_type not in _VALID_NODE_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown node type.")
    items = GraphRepository(session).list_nodes_by_workspace(workspace_id, node_type=node_type, limit=limit, offset=offset)
    count_stmt = select(func.count(GraphNode.id)).where(GraphNode.workspace_id == workspace_id)
    if node_type:
        count_stmt = count_stmt.where(GraphNode.node_type == node_type)
    total = session.scalar(count_stmt) or 0
    return PaginatedGraphNodesResponse(items=[_node_to_response(item) for item in items], total=total, limit=limit, offset=offset)


@router.get("/edges", response_model=PaginatedGraphEdgesResponse)
def list_edges(
    workspace_id: uuid.UUID,
    session: Session = Depends(get_db),
    relationship_type: str | None = Query(default=None, max_length=30),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> PaginatedGraphEdgesResponse:
    if relationship_type and relationship_type not in _VALID_RELATIONSHIPS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown relationship type.")
    items = GraphRepository(session).list_edges_by_workspace(
        workspace_id, relationship_type=relationship_type, limit=limit, offset=offset
    )
    count_stmt = select(func.count(GraphEdge.id)).where(GraphEdge.workspace_id == workspace_id)
    if relationship_type:
        count_stmt = count_stmt.where(GraphEdge.relationship_type == relationship_type)
    total = session.scalar(count_stmt) or 0
    return PaginatedGraphEdgesResponse(items=[_edge_to_response(item) for item in items], total=total, limit=limit, offset=offset)


@router.get("/impact", response_model=ImpactResponse)
def impact(
    workspace_id: uuid.UUID,
    session: Session = Depends(get_db),
    node_id: uuid.UUID = Query(),
    depth: int = Query(default=2, ge=1, le=MAX_DEPTH),
    node_cap: int = Query(default=50, ge=1, le=200),
    edge_cap: int = Query(default=100, ge=1, le=500),
) -> ImpactResponse:
    node = session.get(GraphNode, node_id)
    if node is None or node.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Graph node not found.")
    result = traverse_impact(session, workspace_id, node_id, max_depth=depth, node_cap=node_cap, edge_cap=edge_cap)
    ordered_nodes = sorted(result.nodes.values(), key=lambda item: (item.node_type, item.display_label))
    ordered_edges = sorted(result.edges.values(), key=lambda item: str(item.id))
    return ImpactResponse(
        start_node_id=result.start_node_id, max_depth=depth, truncated=result.truncated,
        reached_nodes=result.reached_node_count,
        nodes=[_node_to_response(item) for item in ordered_nodes],
        edges=[_edge_to_response(item) for item in ordered_edges],
        validated_paths=[ImpactPathResponse(node_ids=list(path.node_ids), edge_ids=list(path.edge_ids), validated=True) for path in result.validated_paths],
        candidate_paths=[ImpactPathResponse(node_ids=list(path.node_ids), edge_ids=list(path.edge_ids), validated=False) for path in result.candidate_paths],
    )
