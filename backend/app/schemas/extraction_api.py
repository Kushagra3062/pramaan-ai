"""
API response schemas for structured extraction (Milestone 4B.4B).

Nothing here returns raw ORM objects or chunk text. Backend-owned provenance stored under
``normalized_value["_pramaan"]`` is split out through an explicit allow-list: the proposal
fingerprint and any other internal key are never exposed, and the model-controlled
``normalized_value`` is returned without the ``_pramaan`` namespace.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.repositories.fact_repo import PROVENANCE_KEY
from app.services.extraction_orchestrator import (
    ExtractionRunResult,
    PageReason,
    PageRunResult,
    PageStatus,
    RunStatus,
)

# ── Evidence and provenance ───────────────────────────────────────────────────


class EvidenceReferenceResponse(BaseModel):
    """Reference from a fact/requirement to a canonical chunk. Never includes chunk text."""

    model_config = ConfigDict(extra="forbid")

    evidence_link_id: uuid.UUID
    chunk_id: uuid.UUID
    chunk_index: int
    page_number: int
    relationship_type: str
    verification_status: str


class ProvenanceResponse(BaseModel):
    """Allow-listed backend-controlled provenance (not model output)."""

    model_config = ConfigDict(extra="forbid")

    extraction_run_id: str | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    extraction_method: str | None = None
    extracted_at: str | None = None
    page_number: int | None = None
    review_status: str | None = None


_PROVENANCE_FIELDS = tuple(ProvenanceResponse.model_fields)


def split_normalized_value(
    normalized_value: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return (model-controlled metadata without ``_pramaan``, raw backend block).

    Builds new dicts; the stored JSONB object is never mutated. A stored value that held only
    backend provenance yields ``None`` model metadata.
    """
    if normalized_value is None:
        return None, {}
    block = normalized_value.get(PROVENANCE_KEY)
    backend = block if isinstance(block, dict) else {}
    clean = {k: v for k, v in normalized_value.items() if k != PROVENANCE_KEY}
    return (clean or None), backend


def build_provenance(backend: dict[str, Any], *, include_review_status: bool) -> ProvenanceResponse | None:
    if not backend:
        return None
    data = {k: backend.get(k) for k in _PROVENANCE_FIELDS if k != "review_status"}
    # str() guards against odd legacy types without exposing structure
    for key in ("extraction_run_id", "model_id", "prompt_version", "extraction_method", "extracted_at"):
        if data[key] is not None:
            data[key] = str(data[key])
    page_number = data.get("page_number")
    data["page_number"] = page_number if isinstance(page_number, int) and not isinstance(page_number, bool) else None
    data["review_status"] = (
        str(backend["review_status"]) if include_review_status and backend.get("review_status") else None
    )
    return ProvenanceResponse(**data)


# ── Facts and requirements ────────────────────────────────────────────────────


class ExtractedFactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: uuid.UUID
    fact_type: str
    raw_value: str
    normalized_value: dict[str, Any] | None = None
    source_quote: str
    page_number: int | None = None
    extraction_method: str
    verification_status: str
    provenance: ProvenanceResponse | None = None
    evidence: list[EvidenceReferenceResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class RequirementResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: uuid.UUID
    title: str
    description: str
    requirement_type: str
    normalized_value: dict[str, Any] | None = None
    status: str
    review_status: str | None = None
    source_quote: str | None = None
    provenance: ProvenanceResponse | None = None
    evidence: list[EvidenceReferenceResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class PaginatedFactsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ExtractedFactResponse]
    total: int
    limit: int
    offset: int


class PaginatedRequirementsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RequirementResponse]
    total: int
    limit: int
    offset: int


# ── Extraction run ────────────────────────────────────────────────────────────


class PageExtractionResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_number: int
    status: PageStatus
    reason: PageReason | None = None
    page_extraction_status: str | None = None
    chunks_processed: int
    requests_succeeded: int
    requests_failed: int
    facts_created: int
    facts_reused: int
    requirements_created: int
    requirements_reused: int
    evidence_links_created: int
    proposals_skipped: int
    skip_reasons: dict[str, int]

    @classmethod
    def from_result(cls, page: PageRunResult) -> PageExtractionResultResponse:
        return cls(
            page_number=page.page_number,
            status=page.status,
            reason=page.reason,
            page_extraction_status=page.page_extraction_status,
            chunks_processed=page.chunks_processed,
            requests_succeeded=page.requests_succeeded,
            requests_failed=page.requests_failed,
            facts_created=page.facts_created,
            facts_reused=page.facts_reused,
            requirements_created=page.requirements_created,
            requirements_reused=page.requirements_reused,
            evidence_links_created=page.evidence_links_created,
            proposals_skipped=page.proposals_skipped,
            skip_reasons=dict(page.skip_reasons),
        )


class ExtractionRunResponse(BaseModel):
    """Typed summary of one document-level extraction run (no source text, prompts or secrets)."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: uuid.UUID
    document_id: uuid.UUID
    extraction_run_id: str
    model_id: str
    prompt_version: str
    started_at: datetime
    completed_at: datetime
    duration_ms: float
    status: RunStatus
    page_count: int
    pages_processed: int
    pages_skipped: int
    pages_failed: int
    pages_not_processed: int
    chunk_requests: int
    model_requests_succeeded: int
    model_requests_failed: int
    facts_created: int
    facts_reused: int
    requirements_created: int
    requirements_reused: int
    evidence_links_created: int
    proposals_skipped: int
    pages: list[PageExtractionResultResponse]

    @classmethod
    def from_result(cls, result: ExtractionRunResult) -> ExtractionRunResponse:
        return cls(
            workspace_id=result.workspace_id,
            document_id=result.document_id,
            extraction_run_id=result.extraction_run_id,
            model_id=result.model_id,
            prompt_version=result.prompt_version,
            started_at=result.started_at,
            completed_at=result.completed_at,
            duration_ms=result.duration_ms,
            status=result.status,
            page_count=result.page_count,
            pages_processed=result.pages_processed,
            pages_skipped=result.pages_skipped,
            pages_failed=result.pages_failed,
            pages_not_processed=result.pages_not_processed,
            chunk_requests=result.chunk_requests,
            model_requests_succeeded=result.model_requests_succeeded,
            model_requests_failed=result.model_requests_failed,
            facts_created=result.facts_created,
            facts_reused=result.facts_reused,
            requirements_created=result.requirements_created,
            requirements_reused=result.requirements_reused,
            evidence_links_created=result.evidence_links_created,
            proposals_skipped=result.proposals_skipped,
            pages=[PageExtractionResultResponse.from_result(p) for p in result.pages],
        )
