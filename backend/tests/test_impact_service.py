"""Database-backed tests for impact traversal: depth cap, cycles, provenance split."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db.repositories.graph_repo import GraphRepository
from app.models.enums import (
    GraphEdgeStatus,
    GraphNodeType,
    GraphProvenanceType,
    GraphRelationshipType,
)
from app.models.workspace import Workspace
from app.services.impact_service import traverse_impact


def _node(repo: GraphRepository, workspace_id, label: str):
    return repo.create_node(
        workspace_id=workspace_id,
        node_type=GraphNodeType.FACT,
        entity_key=f"fact:{label}:{uuid.uuid4().hex[:6]}",
        display_label=label,
    )


def test_traversal_depth_two_and_cycle_safe(db_session: Session, test_workspace: Workspace):
    repo = GraphRepository(db_session)
    wid = test_workspace.id
    a, b, c, d = (_node(repo, wid, label) for label in ("a", "b", "c", "d"))
    repo.create_edge(wid, a.id, b.id, GraphRelationshipType.AFFECTS, GraphProvenanceType.DETERMINISTIC,
                     GraphEdgeStatus.VERIFIED)
    repo.create_edge(wid, b.id, c.id, GraphRelationshipType.AFFECTS, GraphProvenanceType.DETERMINISTIC,
                     GraphEdgeStatus.VERIFIED)
    repo.create_edge(wid, c.id, d.id, GraphRelationshipType.AFFECTS, GraphProvenanceType.DETERMINISTIC,
                     GraphEdgeStatus.VERIFIED)
    repo.create_edge(wid, c.id, a.id, GraphRelationshipType.AFFECTS, GraphProvenanceType.DETERMINISTIC,
                     GraphEdgeStatus.VERIFIED)
    db_session.flush()
    result = traverse_impact(db_session, wid, a.id)
    assert result.reached_node_count == 3  # a, b, c — d is depth 3
    assert d.id not in result.nodes
    assert result.validated_paths
    assert not result.candidate_paths
    assert result.truncated is False


def test_candidate_edges_reported_separately(db_session: Session, test_workspace: Workspace):
    repo = GraphRepository(db_session)
    wid = test_workspace.id
    a, b = _node(repo, wid, "a"), _node(repo, wid, "b")
    repo.create_edge(wid, a.id, b.id, GraphRelationshipType.CONTRADICTS, GraphProvenanceType.MODEL_PROPOSED,
                     GraphEdgeStatus.CANDIDATE)
    db_session.flush()
    result = traverse_impact(db_session, wid, a.id)
    assert not result.validated_paths
    assert len(result.candidate_paths) == 1
    assert result.candidate_paths[0].node_ids == (a.id, b.id)


def test_cross_workspace_nodes_are_invisible(db_session: Session, test_workspace: Workspace):
    repo = GraphRepository(db_session)
    other_id = uuid.uuid4()
    node = _node(repo, test_workspace.id, "a")
    db_session.flush()
    result = traverse_impact(db_session, other_id, node.id)
    assert result.reached_node_count == 0


def test_caps_truncate_without_error(db_session: Session, test_workspace: Workspace):
    repo = GraphRepository(db_session)
    wid = test_workspace.id
    center = _node(repo, wid, "center")
    for index in range(5):
        leaf = _node(repo, wid, f"leaf-{index}")
        repo.create_edge(wid, center.id, leaf.id, GraphRelationshipType.AFFECTS,
                         GraphProvenanceType.DETERMINISTIC, GraphEdgeStatus.VERIFIED)
    db_session.flush()
    result = traverse_impact(db_session, wid, center.id, node_cap=3, edge_cap=100)
    assert result.truncated is True
    assert result.reached_node_count <= 3
