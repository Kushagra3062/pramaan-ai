"""Bounded Evidence-to-Impact traversal (FR-032).

Breadth-first search from one graph node, maximum depth 2, cycle-safe, with
configurable node/edge caps. Only DETERMINISTIC or HUMAN_CONFIRMED edges with
VERIFIED status form validated paths; anything else (MODEL_PROPOSED provenance
or CANDIDATE/REJECTED status) is reported separately and never as validated.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.db.repositories.graph_repo import GraphRepository
from app.models.enums import GraphEdgeStatus, GraphProvenanceType
from app.models.graph import GraphEdge, GraphNode

MAX_DEPTH = 2
DEFAULT_NODE_CAP = 50
DEFAULT_EDGE_CAP = 100

_VALIDATED_PROVENANCE = {GraphProvenanceType.DETERMINISTIC.value, GraphProvenanceType.HUMAN_CONFIRMED.value}


def is_validated_edge(edge: GraphEdge) -> bool:
    return (
        edge.provenance_type in _VALIDATED_PROVENANCE
        and edge.verification_status == GraphEdgeStatus.VERIFIED.value
    )


@dataclass(frozen=True)
class ImpactPath:
    node_ids: tuple[uuid.UUID, ...]
    edge_ids: tuple[uuid.UUID, ...]
    validated: bool


@dataclass
class ImpactResult:
    start_node_id: uuid.UUID
    nodes: dict[uuid.UUID, GraphNode] = field(default_factory=dict)
    edges: dict[uuid.UUID, GraphEdge] = field(default_factory=dict)
    validated_paths: list[ImpactPath] = field(default_factory=list)
    candidate_paths: list[ImpactPath] = field(default_factory=list)
    truncated: bool = False

    @property
    def reached_node_count(self) -> int:
        return len(self.nodes)


def traverse_impact(
    session: Session,
    workspace_id: uuid.UUID,
    start_node_id: uuid.UUID,
    *,
    max_depth: int = MAX_DEPTH,
    node_cap: int = DEFAULT_NODE_CAP,
    edge_cap: int = DEFAULT_EDGE_CAP,
    follow_incoming: bool = True,
    follow_outgoing: bool = True,
) -> ImpactResult:
    """Deterministic BFS over workspace-scoped edges. Read-only."""
    repo = GraphRepository(session)
    result = ImpactResult(start_node_id=start_node_id)
    start = session.get(GraphNode, start_node_id)
    if start is None or start.workspace_id != workspace_id:
        return result
    result.nodes[start.id] = start
    depth = min(max(0, max_depth), MAX_DEPTH)
    frontier: list[tuple[uuid.UUID, tuple[uuid.UUID, ...], tuple[uuid.UUID, ...], bool]] = [(start.id, (start.id,), (), True)]
    visited: set[uuid.UUID] = {start.id}
    for _ in range(depth):
        next_frontier: list[tuple[uuid.UUID, tuple[uuid.UUID, ...], tuple[uuid.UUID, ...], bool]] = []
        for node_id, node_path, edge_path, path_validated in frontier:
            neighbors: list[tuple[GraphEdge, uuid.UUID]] = []
            if follow_outgoing:
                neighbors.extend((edge, edge.target_node_id) for edge in repo.get_out_edges(node_id))
            if follow_incoming:
                neighbors.extend((edge, edge.source_node_id) for edge in repo.get_in_edges(node_id))
            neighbors.sort(key=lambda item: str(item[0].id))
            for edge, neighbor_id in neighbors:
                if edge.workspace_id != workspace_id:
                    continue
                if edge.id in edge_path:
                    continue  # never reuse an edge inside one path (no a→b→a backtracking)
                if len(result.edges) >= edge_cap:
                    result.truncated = True
                    continue
                result.edges.setdefault(edge.id, edge)
                edge_validated = is_validated_edge(edge)
                combined_validated = path_validated and edge_validated
                extended_nodes = node_path + (neighbor_id,)
                extended_edges = edge_path + (edge.id,)
                path = ImpactPath(node_ids=extended_nodes, edge_ids=extended_edges, validated=combined_validated)
                (result.validated_paths if combined_validated else result.candidate_paths).append(path)
                if neighbor_id in visited:
                    continue
                if len(result.nodes) >= node_cap:
                    result.truncated = True
                    continue
                neighbor = session.get(GraphNode, neighbor_id)
                if neighbor is None or neighbor.workspace_id != workspace_id:
                    continue
                result.nodes[neighbor.id] = neighbor
                visited.add(neighbor.id)
                next_frontier.append((neighbor.id, extended_nodes, extended_edges, combined_validated))
        frontier = next_frontier
        if not frontier:
            break
    return result
