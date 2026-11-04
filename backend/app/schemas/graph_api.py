"""Response schemas for the Evidence-to-Impact Graph read API (FR-031/FR-032)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class GraphNodeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    node_type: str
    entity_key: str
    display_label: str
    metadata: dict[str, Any]
    created_at: datetime


class GraphEdgeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    source_node_id: uuid.UUID
    target_node_id: uuid.UUID
    relationship_type: str
    provenance_type: str
    verification_status: str
    confidence: float | None = None
    validated: bool
    metadata: dict[str, Any]


class ImpactPathResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_ids: list[uuid.UUID]
    edge_ids: list[uuid.UUID]
    validated: bool


class ImpactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_node_id: uuid.UUID
    max_depth: int
    truncated: bool
    reached_nodes: int
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]
    validated_paths: list[ImpactPathResponse]
    candidate_paths: list[ImpactPathResponse]


class PaginatedGraphNodesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[GraphNodeResponse]
    total: int
    limit: int
    offset: int


class PaginatedGraphEdgesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[GraphEdgeResponse]
    total: int
    limit: int
    offset: int
