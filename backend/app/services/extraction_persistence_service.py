"""
Safe, idempotent persistence of validated structured-extraction proposals.

Responsibilities (and only these):
  * Re-verify every proposal's evidence quote deterministically against the canonical
    page text and map it to exactly one canonical chunk (Phase 4B.3A verifier).
  * Create facts / requirements / evidence links with initial *unreviewed* states.
  * Reuse existing records for identical proposals (fingerprint in JSONB); never delete,
    overwrite, downgrade or re-status anything that already exists.

Transaction contract:
  The service FLUSHES but NEVER COMMITS. The document row is locked (SELECT ... FOR UPDATE)
  before any lookup, and the lock is held until the caller's outer transaction ends. All
  writes for one call happen inside a SAVEPOINT, so a failure rolls back that call's writes
  only. The orchestration service owns commit/rollback, ModelUsage and AuditEvent records.

An exact evidence match proves only that the quote is present in canonical text. It is
never human approval: facts and evidence links start UNVERIFIED, requirements start
DRAFT with review_status PENDING_REVIEW.

The model never supplies database identifiers; document/page/chunk records come from the
database via trusted workspace/document/page_number arguments.
"""

from __future__ import annotations

import hashlib
import json
import logging
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import metrics
from app.db.repositories.analysis_repo import AnalysisRepository
from app.db.repositories.document_repo import DocumentPageRepository, DocumentRepository
from app.db.repositories.fact_repo import (
    PROVENANCE_KEY,
    EvidenceLinkRepository,
    FactRepository,
    RequirementRepository,
)
from app.ingestion.chunking import clean_canonical_text
from app.ingestion.evidence_verifier import (
    CanonicalChunkRef,
    ChunkMatchOutcome,
    find_matching_canonical_chunks,
    verify_evidence_quote,
)
from app.models.document import DocumentChunk
from app.models.enums import (
    DocumentProcessingStatus,
    EvidenceRelationshipType,
    RequirementStatus,
    VerificationStatus,
)
from app.schemas.extraction import (
    ExtractedFactCandidate,
    RequirementCandidate,
    StructuredExtractionResponse,
)

logger = logging.getLogger(__name__)

EXTRACTION_METHOD = "LLM_STRUCTURED_EXTRACTION"
FINGERPRINT_VERSION = "1"
REQUIREMENT_TITLE_MAX_LENGTH = 255  # requirements.title is String(255)
REVIEW_STATUS_PENDING = "PENDING_REVIEW"


# ── Errors (whole-call problems; per-proposal problems become SKIPPED results) ─


class ExtractionPersistenceError(Exception):
    """Base class for persistence failures."""


class DocumentNotAvailableError(ExtractionPersistenceError):
    """Document missing in this workspace, or deleted/being deleted."""


class PageNotFoundError(ExtractionPersistenceError):
    """The page does not exist for this document."""


class PageNotCanonicalError(ExtractionPersistenceError):
    """The page has no successfully extracted canonical text."""


# ── Inputs / outputs ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PersistenceProvenance:
    """Backend-controlled run metadata (not model output)."""

    model_id: str
    prompt_version: str
    extraction_run_id: str | None = None
    extracted_at: datetime | None = None


class ProposalKind(StrEnum):
    FACT = "FACT"
    REQUIREMENT = "REQUIREMENT"


class ProposalOutcome(StrEnum):
    CREATED = "CREATED"
    REUSED = "REUSED"
    SKIPPED = "SKIPPED"


class SkipReason(StrEnum):
    INVALID_QUOTE = "INVALID_QUOTE"
    QUOTE_NOT_IN_PAGE = "QUOTE_NOT_IN_PAGE"
    QUOTE_IN_PAGE_NO_CHUNK = "QUOTE_IN_PAGE_NO_CHUNK"
    AMBIGUOUS_CHUNK_MATCH = "AMBIGUOUS_CHUNK_MATCH"
    TITLE_TOO_LONG = "TITLE_TOO_LONG"
    FACT_VALUE_NOT_IN_EVIDENCE = "FACT_VALUE_NOT_IN_EVIDENCE"


_SKIP_BY_MATCH = {
    ChunkMatchOutcome.INVALID_INPUT: SkipReason.INVALID_QUOTE,
    ChunkMatchOutcome.QUOTE_NOT_IN_PAGE: SkipReason.QUOTE_NOT_IN_PAGE,
    ChunkMatchOutcome.QUOTE_IN_PAGE_NO_CHUNK: SkipReason.QUOTE_IN_PAGE_NO_CHUNK,
    ChunkMatchOutcome.MULTIPLE_CHUNK_MATCHES: SkipReason.AMBIGUOUS_CHUNK_MATCH,
}


@dataclass(frozen=True)
class ProposalResult:
    kind: ProposalKind
    index: int  # position in the response's facts / requirements list
    outcome: ProposalOutcome
    entity_id: uuid.UUID | None = None
    chunk_id: uuid.UUID | None = None
    evidence_link_id: uuid.UUID | None = None
    evidence_link_created: bool = False
    fingerprint: str | None = None
    skip_reason: SkipReason | None = None


@dataclass
class PersistenceResult:
    results: list[ProposalResult] = field(default_factory=list)

    def _count(self, kind: ProposalKind | None, outcome: ProposalOutcome) -> int:
        return sum(1 for r in self.results if r.outcome is outcome and (kind is None or r.kind is kind))

    @property
    def facts_created(self) -> int:
        return self._count(ProposalKind.FACT, ProposalOutcome.CREATED)

    @property
    def facts_reused(self) -> int:
        return self._count(ProposalKind.FACT, ProposalOutcome.REUSED)

    @property
    def facts_skipped(self) -> int:
        return self._count(ProposalKind.FACT, ProposalOutcome.SKIPPED)

    @property
    def requirements_created(self) -> int:
        return self._count(ProposalKind.REQUIREMENT, ProposalOutcome.CREATED)

    @property
    def requirements_reused(self) -> int:
        return self._count(ProposalKind.REQUIREMENT, ProposalOutcome.REUSED)

    @property
    def requirements_skipped(self) -> int:
        return self._count(ProposalKind.REQUIREMENT, ProposalOutcome.SKIPPED)

    @property
    def evidence_links_created(self) -> int:
        return sum(1 for r in self.results if r.evidence_link_created)


# ── Fingerprints ──────────────────────────────────────────────────────────────


def _nfc(value: Any) -> Any:
    """Recursively NFC-normalize strings so equivalent Unicode forms fingerprint identically."""
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        return {_nfc(k): _nfc(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_nfc(v) for v in value]
    return value


def compute_fingerprint(payload: dict[str, Any]) -> str:
    """SHA-256 over canonical JSON (sorted keys, compact, non-ASCII preserved)."""
    canonical = json.dumps(
        _nfc(payload), sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fact_fingerprint(
    document_id: uuid.UUID, page_id: uuid.UUID, candidate: ExtractedFactCandidate
) -> str:
    return compute_fingerprint(
        {
            "v": FINGERPRINT_VERSION,
            "kind": "fact",
            "document_id": str(document_id),
            "page_id": str(page_id),
            "fact_type": candidate.fact_type.value,
            "raw_value": candidate.raw_value,
            "source_quote": candidate.source_quote,
            "normalized_value": _model_metadata(candidate.normalized_value),
            "description": candidate.description,
        }
    )


def requirement_fingerprint(
    document_id: uuid.UUID, page_id: uuid.UUID, candidate: RequirementCandidate
) -> str:
    return compute_fingerprint(
        {
            "v": FINGERPRINT_VERSION,
            "kind": "requirement",
            "document_id": str(document_id),
            "page_id": str(page_id),
            "requirement_type": candidate.requirement_type.value,
            "title": candidate.title,
            "description": candidate.description,
            "source_quote": candidate.source_quote,
            "normalized_value": _requirement_metadata(candidate),
        }
    )


def _model_metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    """Model-controlled metadata with any attempt to spoof the backend namespace removed."""
    return {k: v for k, v in (value or {}).items() if k != PROVENANCE_KEY}


def _requirement_metadata(candidate: RequirementCandidate) -> dict[str, Any]:
    if candidate.normalized_value is None:
        return {}
    return _model_metadata(candidate.normalized_value.model_dump(mode="json"))


# ── Service ───────────────────────────────────────────────────────────────────


def persist_page_extraction(
    session: Session,
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    page_number: int,
    response: StructuredExtractionResponse,
    provenance: PersistenceProvenance,
) -> PersistenceResult:
    """Persist one page's validated proposals. Flushes only; the caller commits."""
    doc_repo = DocumentRepository(session)
    # Lock first; held until the caller's outer transaction ends.
    document = doc_repo.get_for_update(workspace_id, document_id)
    if (
        document is None
        or document.deleted_at is not None
        or document.processing_status
        in (DocumentProcessingStatus.DELETING.value, DocumentProcessingStatus.DELETED.value)
    ):
        raise DocumentNotAvailableError("Document not available for extraction persistence")

    page = DocumentPageRepository(session).get_by_page_number(document_id, page_number)
    if page is None or page.document_id != document_id:
        raise PageNotFoundError("Page not found for document")
    page_text = clean_canonical_text(page.extracted_text or "")
    if page.extraction_status != "COMPLETED" or not page_text:
        raise PageNotCanonicalError("Page has no successfully extracted canonical text")

    chunk_rows = list(
        session.scalars(
            select(DocumentChunk)
            .where(
                DocumentChunk.workspace_id == workspace_id,
                DocumentChunk.document_id == document_id,
                DocumentChunk.page_start == page_number,
                DocumentChunk.page_end == page_number,
            )
            .order_by(DocumentChunk.chunk_index.asc())
        ).all()
    )
    chunk_refs = [
        CanonicalChunkRef(
            chunk_id=c.id,
            chunk_index=c.chunk_index,
            page_start=c.page_start,
            page_end=c.page_end,
            content=c.content,
        )
        for c in chunk_rows
    ]

    extracted_at = (provenance.extracted_at or datetime.now(UTC)).isoformat()
    fact_repo = FactRepository(session)
    req_repo = RequirementRepository(session)
    link_repo = EvidenceLinkRepository(session)
    analysis_repo = AnalysisRepository(session)

    def provenance_block(fingerprint: str, chunk_id: uuid.UUID) -> dict[str, Any]:
        return {
            "proposal_fingerprint": fingerprint,
            "fingerprint_version": FINGERPRINT_VERSION,
            "model_id": provenance.model_id,
            "prompt_version": provenance.prompt_version,
            "extraction_method": EXTRACTION_METHOD,
            "extraction_run_id": provenance.extraction_run_id,
            "extracted_at": extracted_at,
            "document_id": str(document_id),
            "page_id": str(page.id),
            "page_number": page_number,
            "chunk_id": str(chunk_id),
        }

    def locate(quote: str) -> tuple[uuid.UUID | None, SkipReason | None]:
        match = find_matching_canonical_chunks(quote, page_number, page_text, chunk_refs)
        if match.outcome is ChunkMatchOutcome.SINGLE_CHUNK_MATCH:
            return match.matching_chunk_ids[0], None
        return None, _SKIP_BY_MATCH[match.outcome]

    result = PersistenceResult()

    # SAVEPOINT: a failure undoes this call's writes without touching unrelated outer work.
    with session.begin_nested():
        for index, fact in enumerate(response.facts):
            chunk_id, skip = locate(fact.source_quote)
            if chunk_id is None:
                result.results.append(
                    ProposalResult(ProposalKind.FACT, index, ProposalOutcome.SKIPPED, skip_reason=skip)
                )
                continue
            # A fact's literal raw_value must itself appear in its own supporting quote
            # (same NFC + whitespace policy; exact, case-sensitive). Not applied to
            # requirements, whose title/description are interpretations.
            if not verify_evidence_quote(fact.source_quote, fact.raw_value).is_exact_match:
                result.results.append(
                    ProposalResult(
                        ProposalKind.FACT,
                        index,
                        ProposalOutcome.SKIPPED,
                        skip_reason=SkipReason.FACT_VALUE_NOT_IN_EVIDENCE,
                    )
                )
                continue
            fp = fact_fingerprint(document_id, page.id, fact)
            existing = fact_repo.find_by_fingerprint(workspace_id, document_id, fp)
            created = existing is None
            if existing is None:
                normalized = _model_metadata(fact.normalized_value)
                if fact.description is not None and "description" not in normalized:
                    normalized["description"] = fact.description
                normalized[PROVENANCE_KEY] = provenance_block(fp, chunk_id)
                existing = fact_repo.create(
                    workspace_id=workspace_id,
                    document_id=document_id,
                    page_id=page.id,
                    fact_type=fact.fact_type,
                    raw_value=fact.raw_value,
                    source_quote=fact.source_quote,
                    extraction_method=EXTRACTION_METHOD,
                    normalized_value=normalized,
                    verification_status=VerificationStatus.UNVERIFIED,
                    graph_node_id=None,
                )
            link = link_repo.find_for_target(workspace_id, chunk_id, fact_id=existing.id)
            link_created = link is None
            if link is None:
                link = analysis_repo.add_evidence_link(
                    workspace_id=workspace_id,
                    chunk_id=chunk_id,
                    fact_id=existing.id,
                    relationship_type=EvidenceRelationshipType.SUPPORTS,
                    verification_status=VerificationStatus.UNVERIFIED,
                )
            result.results.append(
                ProposalResult(
                    ProposalKind.FACT,
                    index,
                    ProposalOutcome.CREATED if created else ProposalOutcome.REUSED,
                    entity_id=existing.id,
                    chunk_id=chunk_id,
                    evidence_link_id=link.id,
                    evidence_link_created=link_created,
                    fingerprint=fp,
                )
            )

        for index, req in enumerate(response.requirements):
            if len(req.title) > REQUIREMENT_TITLE_MAX_LENGTH:
                result.results.append(
                    ProposalResult(
                        ProposalKind.REQUIREMENT,
                        index,
                        ProposalOutcome.SKIPPED,
                        skip_reason=SkipReason.TITLE_TOO_LONG,
                    )
                )
                continue
            chunk_id, skip = locate(req.source_quote)
            if chunk_id is None:
                result.results.append(
                    ProposalResult(
                        ProposalKind.REQUIREMENT, index, ProposalOutcome.SKIPPED, skip_reason=skip
                    )
                )
                continue
            fp = requirement_fingerprint(document_id, page.id, req)
            existing_req = req_repo.find_by_fingerprint(workspace_id, fp)
            created = existing_req is None
            if existing_req is None:
                normalized = _requirement_metadata(req)
                block = provenance_block(fp, chunk_id)
                block["review_status"] = REVIEW_STATUS_PENDING
                block["source_quote"] = req.source_quote
                normalized[PROVENANCE_KEY] = block
                existing_req = req_repo.create(
                    workspace_id=workspace_id,
                    title=req.title,
                    description=req.description,
                    requirement_type=req.requirement_type.value,
                    normalized_value=normalized,
                    status=RequirementStatus.DRAFT,
                    graph_node_id=None,
                )
            link = link_repo.find_for_target(workspace_id, chunk_id, requirement_id=existing_req.id)
            link_created = link is None
            if link is None:
                link = analysis_repo.add_evidence_link(
                    workspace_id=workspace_id,
                    chunk_id=chunk_id,
                    requirement_id=existing_req.id,
                    relationship_type=EvidenceRelationshipType.SUPPORTS,
                    verification_status=VerificationStatus.UNVERIFIED,
                )
            result.results.append(
                ProposalResult(
                    ProposalKind.REQUIREMENT,
                    index,
                    ProposalOutcome.CREATED if created else ProposalOutcome.REUSED,
                    entity_id=existing_req.id,
                    chunk_id=chunk_id,
                    evidence_link_id=link.id,
                    evidence_link_created=link_created,
                    fingerprint=fp,
                )
            )

    # Evidence-verification failures (quote not in the page, ambiguous chunk match, ...) are
    # counted by typed reason only: no quote, candidate or rejected value is ever emitted.
    skip_counts = Counter(
        proposal.skip_reason.value
        for proposal in result.results
        if proposal.skip_reason is not None
    )
    for reason, count in skip_counts.items():
        metrics.record_metric(
            metrics.METRIC_EVIDENCE_FAILURES, value=count, labels={"reason": reason}
        )

    logger.info(
        "extraction_persisted",
        extra={
            "event": "extraction.persisted",
            "document_id": str(document_id),
            "page_number": page_number,
            "facts_created": result.facts_created,
            "facts_reused": result.facts_reused,
            "requirements_created": result.requirements_created,
            "requirements_reused": result.requirements_reused,
            "skipped": result.facts_skipped + result.requirements_skipped,
            "evidence_verification_failures": sum(skip_counts.values()),
            "evidence_failure_reasons": dict(skip_counts),
        },
    )
    return result
