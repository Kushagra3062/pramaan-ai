"""Deterministic conflict candidate detection and human resolution workflow.

Detection is purely rule-based over persisted facts: same fact type, different
normalized values, from different documents. It never calls the model, never
picks a winner, and never auto-resolves. Every candidate keeps both source fact
IDs and passages so the UI can render the FR-016 side-by-side view.
"""

from __future__ import annotations

import logging
import unicodedata
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.repositories.audit_eval_repo import AuditRepository
from app.db.repositories.fact_repo import FactRepository
from app.db.repositories.governance_repo import ConflictRepository
from app.models.enums import ConflictSeverity, ConflictStatus, ConflictType, FactType
from app.models.governance import Conflict

logger = logging.getLogger(__name__)

MAX_FACTS_SCANNED = 200
MAX_PAIRS_CREATED_PER_RUN = 25

_TYPE_TO_CONFLICT: dict[str, ConflictType] = {
    FactType.DATE.value: ConflictType.DEADLINE_MISMATCH,
    FactType.DEADLINE.value: ConflictType.DEADLINE_MISMATCH,
    FactType.AMOUNT.value: ConflictType.NUMERIC_MISMATCH,
    FactType.IDENTIFIER.value: ConflictType.NUMERIC_MISMATCH,
    FactType.RESPONSIBLE_DEPARTMENT.value: ConflictType.RESPONSIBILITY_OVERLAP,
    FactType.OBLIGATION.value: ConflictType.DIRECT_CONTRADICTION,
    FactType.APPROVAL_CONDITION.value: ConflictType.SCOPE_DISCREPANCY,
    FactType.CURRENCY.value: ConflictType.NUMERIC_MISMATCH,
}


def _normalize_value(raw: str) -> str:
    collapsed = " ".join(unicodedata.normalize("NFC", raw or "").split())
    return collapsed.casefold()


@dataclass(frozen=True)
class DetectionSummary:
    facts_scanned: int
    pairs_compared: int
    candidates_created: int
    candidates_skipped_existing: int


def detect_conflict_candidates(
    session: Session,
    workspace_id: uuid.UUID,
    *,
    requested_by: uuid.UUID | None = None,
    request_id: str | None = None,
) -> tuple[DetectionSummary, list[Conflict]]:
    """Create OPEN conflict candidates from cross-document value mismatches.

    Flushes but does not commit; the caller owns the transaction boundary.
    """
    facts = FactRepository(session).list_by_workspace(workspace_id, limit=MAX_FACTS_SCANNED)
    repo = ConflictRepository(session)
    created: list[Conflict] = []
    skipped = 0
    compared = 0
    by_type: dict[str, list] = {}
    for fact in facts:
        if fact.fact_type in _TYPE_TO_CONFLICT:
            by_type.setdefault(fact.fact_type, []).append(fact)
    for fact_type, group in by_type.items():
        for index, first in enumerate(group):
            for second in group[index + 1:]:
                compared += 1
                if first.document_id == second.document_id:
                    continue
                if _normalize_value(first.raw_value) == _normalize_value(second.raw_value):
                    continue
                if repo.get_by_fact_pair(workspace_id, first.id, second.id) is not None:
                    skipped += 1
                    continue
                if len(created) >= MAX_PAIRS_CREATED_PER_RUN:
                    break
                conflict = repo.create(
                    workspace_id=workspace_id,
                    fact_a_id=first.id,
                    fact_b_id=second.id,
                    conflict_type=_TYPE_TO_CONFLICT[fact_type],
                    explanation=(
                        f"Deterministic candidate: two {fact_type} facts from different "
                        f"documents carry different values. Human review required; no "
                        f"winner selected."
                    ),
                    severity=ConflictSeverity.MEDIUM,
                    status=ConflictStatus.OPEN,
                )
                created.append(conflict)
    summary = DetectionSummary(
        facts_scanned=len(facts),
        pairs_compared=compared,
        candidates_created=len(created),
        candidates_skipped_existing=skipped,
    )
    AuditRepository(session).log(
        workspace_id=workspace_id,
        actor_user_id=requested_by,
        event_type="conflict.detection_run",
        entity_type="workspace",
        entity_id=workspace_id,
        request_id=request_id,
        metadata={
            "facts_scanned": summary.facts_scanned,
            "pairs_compared": summary.pairs_compared,
            "candidates_created": summary.candidates_created,
            "candidates_skipped_existing": summary.candidates_skipped_existing,
            "origin": "deterministic",
        },
    )
    logger.info(
        "conflict_detection_run",
        extra={
            "event": "conflict.detection_run",
            "workspace_id": str(workspace_id),
            "candidates_created": summary.candidates_created,
        },
    )
    return summary, created
