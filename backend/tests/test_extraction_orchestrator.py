"""Tests for document-level extraction orchestration (Milestone 4B.3C).

The StructuredExtractor is always replaced by a scripted fake; nothing here can reach Meta.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import event, func, select

from app.core.config import get_settings
from app.db.repositories.document_repo import DocumentChunkRepository, DocumentPageRepository
from app.ingestion.structured_extractor import (
    EmptyResponseError,
    ExtractionAPIStatusError,
    ExtractionJSONParseError,
    ExtractionRateLimitError,
    ExtractionRefusalError,
    ExtractionTimeoutError,
    ExtractionTruncatedError,
    ExtractionUnexpectedFinishReasonError,
    ExtractionUnexpectedToolCallsError,
    ExtractionValidationError,
    ResponseDiagnostics,
    StructuredExtractionResult,
)
from app.models.audit_eval import AuditEvent, ModelUsage
from app.models.document import DocumentChunk
from app.models.enums import RequirementStatus, VerificationStatus
from app.models.evidence_analysis import EvidenceLink
from app.models.fact_requirement import ExtractedFact, Requirement
from app.models.workspace import Workspace
from app.schemas.extraction import StructuredExtractionResponse
from app.services import extraction_orchestrator as orch
from app.services.document_service import (
    DocumentNotFoundError,
    EvidenceLinksExistError,
    regenerate_document_chunks,
)
from app.services.extraction_orchestrator import (
    ExtractionRunAbortedError,
    PageReason,
    PageStatus,
    RunStatus,
    run_document_extraction,
)
from tests.test_extraction_persistence import PAGE1, QUOTE, fact_payload, make_doc, req_payload

CHUNK_WITH_QUOTE = f"Intro paragraph. {QUOTE}"
TAIL = "Closing remarks."


@pytest.fixture(autouse=True)
def _no_real_meta_client(monkeypatch):
    def boom():
        raise AssertionError("real Meta client must never be used in tests")

    monkeypatch.setattr("app.ingestion.structured_extractor.get_meta_client", boom)


def response(facts=(), reqs=()):
    return StructuredExtractionResponse.model_validate({"facts": list(facts), "requirements": list(reqs)})


def ok(facts=(), reqs=(), *, tokens=(10, 5), rid="req-1", latency=12.5):
    return StructuredExtractionResult(
        response=response(facts, reqs),
        model_id="muse-spark-1.3",
        prompt_version="v1.1-structured-extraction",
        latency_ms=latency,
        request_id=rid,
        input_tokens=tokens[0] if tokens else None,
        output_tokens=tokens[1] if tokens else None,
    )


class FakeExtractor:
    """Scripted extractor: `script` maps chunk text -> result | exception (default: empty success)."""

    model_id = "muse-spark-1.3"
    prompt_version = "v1.1-structured-extraction"

    def __init__(self, script=None, on_call=None):
        self.script = script or {}
        self.calls: list[str] = []
        self.on_call = on_call

    def extract_from_text(self, source_text):
        self.calls.append(source_text)
        if self.on_call:
            self.on_call()
        outcome = self.script.get(source_text, ok())
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def run(db, ws, doc, extractor):
    return run_document_extraction(db, workspace_id=ws.id, document_id=doc.id, extractor=extractor)


def count(db, model, **filters):
    stmt = select(func.count()).select_from(model)
    for k, v in filters.items():
        stmt = stmt.where(getattr(model, k) == v)
    return db.scalar(stmt)


@pytest.fixture
def doc(db_session, test_workspace):
    return make_doc(db_session, test_workspace, [(PAGE1, [CHUNK_WITH_QUOTE, TAIL])])


@pytest.fixture
def commits(db_session, monkeypatch):
    calls = {"commit": 0, "rollback": 0}
    real_commit, real_rollback = db_session.commit, db_session.rollback

    def commit():
        calls["commit"] += 1
        return real_commit()

    def rollback():
        calls["rollback"] += 1
        return real_rollback()

    monkeypatch.setattr(db_session, "commit", commit)
    monkeypatch.setattr(db_session, "rollback", rollback)
    return calls


# ── retrieval ─────────────────────────────────────────────────────────────────
def test_valid_document_end_to_end(db_session, test_workspace, doc, commits):
    fx = FakeExtractor({CHUNK_WITH_QUOTE: ok([fact_payload()], [req_payload()])})
    result = run(db_session, test_workspace, doc, fx)
    assert result.status is RunStatus.COMPLETED
    assert (result.pages_processed, result.chunk_requests) == (1, 2)
    assert (result.facts_created, result.requirements_created, result.evidence_links_created) == (1, 1, 2)
    # only canonical chunk content reaches the model, in order, with no backend ids
    assert fx.calls == [CHUNK_WITH_QUOTE, TAIL]
    assert not any(str(doc.id) in c or str(test_workspace.id) in c for c in fx.calls)
    fact = db_session.scalars(select(ExtractedFact).where(ExtractedFact.document_id == doc.id)).one()
    assert fact.verification_status == VerificationStatus.UNVERIFIED.value
    req = db_session.scalars(select(Requirement).where(Requirement.workspace_id == test_workspace.id)).one()
    assert req.status == RequirementStatus.DRAFT.value
    assert commits["commit"] == 1  # exactly one commit, owned by the orchestrator


def test_missing_document_rejected(db_session, test_workspace):
    fx = FakeExtractor()
    with pytest.raises(DocumentNotFoundError):
        run_document_extraction(
            db_session, workspace_id=test_workspace.id, document_id=uuid.uuid4(), extractor=fx
        )
    assert fx.calls == [] and count(db_session, AuditEvent, workspace_id=test_workspace.id) == 0


def test_cross_workspace_document_rejected(db_session, test_workspace, doc):
    other = Workspace(name="Other", slug=f"o-{uuid.uuid4().hex[:8]}")
    db_session.add(other)
    db_session.flush()
    fx = FakeExtractor()
    with pytest.raises(DocumentNotFoundError):
        run_document_extraction(db_session, workspace_id=other.id, document_id=doc.id, extractor=fx)
    assert fx.calls == []


def test_document_without_pages_is_failed_run_with_audit(db_session, test_workspace):
    d = make_doc(db_session, test_workspace, [])
    fx = FakeExtractor()
    result = run(db_session, test_workspace, d, fx)
    assert result.status is RunStatus.FAILED and result.pages == [] and fx.calls == []
    ev = db_session.scalars(select(AuditEvent).where(AuditEvent.entity_id == d.id)).one()
    assert ev.event_type == "document.extraction_failed"


def test_pages_and_chunks_processed_in_stable_order(db_session, test_workspace):
    d = make_doc(db_session, test_workspace, [("Page one text here.", []), ("Page two text here.", [])])
    chunk_repo = DocumentChunkRepository(db_session)
    # inserted out of order on purpose; chunk_index decides
    for page, idx, content in [(2, 3, "p2 second"), (1, 1, "p1 second"), (2, 2, "p2 first"), (1, 0, "p1 first")]:
        chunk_repo.create(
            workspace_id=test_workspace.id, document_id=d.id, page_start=page, page_end=page,
            chunk_index=idx, content=content, embedding_model="m", embedding_version="1",
            content_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        )
    fx = FakeExtractor()
    result = run(db_session, test_workspace, d, fx)
    assert fx.calls == ["p1 first", "p1 second", "p2 first", "p2 second"]
    assert [p.page_number for p in result.pages] == [1, 2]


# ── canonical page / chunk selection ──────────────────────────────────────────
def test_only_completed_nonempty_chunked_pages_processed(db_session, test_workspace):
    d = make_doc(
        db_session,
        test_workspace,
        [
            ("Good page text here.", ["good chunk"]),
            ("Needs ocr text.", ["ocr chunk"]),
            ("Failed page text.", ["failed chunk"]),
            ("", ["empty chunk"]),
            ("Unchunked page text.", []),
        ],
    )
    pages = {p.page_number: p for p in DocumentPageRepository(db_session).list_by_document(d.id)}
    pages[2].extraction_status = "NEEDS_OCR"
    pages[3].extraction_status = "FAILED"
    db_session.flush()
    fx = FakeExtractor()
    result = run(db_session, test_workspace, d, fx)
    assert fx.calls == ["good chunk"]
    by = {p.page_number: p for p in result.pages}
    assert by[1].status is PageStatus.PROCESSED
    assert (by[2].status, by[2].reason, by[2].page_extraction_status) == (
        PageStatus.SKIPPED, PageReason.PAGE_NOT_COMPLETED, "NEEDS_OCR")
    assert by[3].reason is PageReason.PAGE_NOT_COMPLETED and by[3].page_extraction_status == "FAILED"
    assert by[4].reason is PageReason.EMPTY_CANONICAL_TEXT
    assert by[5].reason is PageReason.NO_CANONICAL_CHUNKS
    assert result.status is RunStatus.PARTIAL  # never reported as fully processed
    assert (result.pages_processed, result.pages_skipped) == (1, 4)


def test_foreign_and_cross_page_chunks_ignored(db_session, test_workspace):
    d = make_doc(db_session, test_workspace, [("Page one text here.", ["own chunk"]), ("Page two text here.", [])])
    other_doc = make_doc(db_session, test_workspace, [("Other doc text here.", ["other doc chunk"])])
    other_ws = Workspace(name="O", slug=f"o-{uuid.uuid4().hex[:8]}")
    db_session.add(other_ws)
    db_session.flush()
    repo = DocumentChunkRepository(db_session)
    common = {"embedding_model": "m", "embedding_version": "1"}
    # another workspace's chunk claiming this document and page; and a cross-page chunk
    repo.create(workspace_id=other_ws.id, document_id=d.id, page_start=1, page_end=1, chunk_index=10,
                content="foreign workspace chunk", content_hash="a" * 64, **common)
    repo.create(workspace_id=test_workspace.id, document_id=d.id, page_start=1, page_end=2, chunk_index=11,
                content="cross page chunk", content_hash="b" * 64, **common)
    fx = FakeExtractor()
    result = run(db_session, test_workspace, d, fx)
    assert fx.calls == ["own chunk"]
    assert {p.page_number: p.reason for p in result.pages}[2] is PageReason.NO_CANONICAL_CHUNKS
    assert other_doc.id != d.id


# ── extraction and aggregation ────────────────────────────────────────────────
def test_empty_model_response_is_successful_extraction(db_session, test_workspace, doc):
    result = run(db_session, test_workspace, doc, FakeExtractor())
    assert result.status is RunStatus.COMPLETED
    assert result.model_requests_succeeded == 2 and result.facts_created == 0
    assert count(db_session, ModelUsage, workspace_id=test_workspace.id) == 2


def test_chunk_responses_aggregated_into_one_persistence_call(db_session, test_workspace, doc, monkeypatch):
    seen = []
    real = orch.persist_page_extraction

    def spy(session, **kw):
        seen.append((kw["page_number"], len(kw["response"].facts), len(kw["response"].requirements)))
        return real(session, **kw)

    monkeypatch.setattr(orch, "persist_page_extraction", spy)
    fx = FakeExtractor({
        CHUNK_WITH_QUOTE: ok([fact_payload()]),
        TAIL: ok(reqs=[req_payload(quote=TAIL, title="Closing")]),
    })
    result = run(db_session, test_workspace, doc, fx)
    assert seen == [(1, 1, 1)]
    assert (result.facts_created, result.requirements_created) == (1, 1)


def test_duplicate_candidates_from_overlapping_calls_deduplicated(db_session, test_workspace, doc):
    fx = FakeExtractor({CHUNK_WITH_QUOTE: ok([fact_payload()]), TAIL: ok([fact_payload()])})
    result = run(db_session, test_workspace, doc, fx)
    assert (result.facts_created, result.facts_reused) == (1, 1)
    assert count(db_session, ExtractedFact, document_id=doc.id) == 1


def test_ambiguous_evidence_is_skipped_not_guessed(db_session, test_workspace):
    d = make_doc(db_session, test_workspace, [(PAGE1, [f"A {QUOTE}", f"{QUOTE} B"])])
    fx = FakeExtractor({f"A {QUOTE}": ok([fact_payload()], [req_payload()])})
    result = run(db_session, test_workspace, d, fx)
    assert result.proposals_skipped == 2 and result.facts_created == 0
    assert result.pages[0].skip_reasons == {"AMBIGUOUS_CHUNK_MATCH": 2}
    assert count(db_session, EvidenceLink, workspace_id=test_workspace.id) == 0


def test_invalid_quote_counted_as_skipped(db_session, test_workspace, doc):
    fx = FakeExtractor({CHUNK_WITH_QUOTE: ok([fact_payload(quote="Not on the page at all.", raw="Not")])})
    result = run(db_session, test_workspace, doc, fx)
    assert result.proposals_skipped == 1 and result.facts_created == 0
    assert result.status is RunStatus.COMPLETED  # page was fully processed; the proposal was rejected


# ── failures ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (ExtractionValidationError("bad schema"), PageReason.SCHEMA_VALIDATION_FAILED),
        (ExtractionJSONParseError("bad json"), PageReason.INVALID_JSON),
        (EmptyResponseError("empty"), PageReason.EMPTY_RESPONSE),
    ],
)
def test_malformed_response_fails_page_without_partial_persistence(db_session, test_workspace, exc, reason):
    d = make_doc(db_session, test_workspace, [(PAGE1, [CHUNK_WITH_QUOTE, TAIL]), ("Second page text here.", ["p2"])])
    fx = FakeExtractor({CHUNK_WITH_QUOTE: ok([fact_payload()]), TAIL: exc})
    result = run(db_session, test_workspace, d, fx)
    p1, p2 = result.pages
    assert (p1.status, p1.reason) == (PageStatus.FAILED, reason)
    assert (p1.requests_succeeded, p1.requests_failed) == (1, 1)
    assert count(db_session, ExtractedFact, document_id=d.id) == 0  # first chunk's fact NOT persisted
    assert p2.status is PageStatus.PROCESSED and fx.calls[-1] == "p2"  # page-local failure: run continues
    assert result.status is RunStatus.PARTIAL
    statuses = sorted(u.status for u in db_session.scalars(select(ModelUsage)).all()
                      if u.workspace_id == test_workspace.id)
    assert statuses == ["ERROR", "SUCCESS", "SUCCESS"]


def test_systemic_failure_stops_further_requests(db_session, test_workspace):
    d = make_doc(db_session, test_workspace, [("Page one text here.", ["p1a", "p1b"]), ("Page two text here.", ["p2"])])
    fx = FakeExtractor({"p1a": ExtractionRateLimitError("slow down")})
    result = run(db_session, test_workspace, d, fx)
    assert fx.calls == ["p1a"]
    assert (result.pages[0].status, result.pages[0].reason) == (PageStatus.FAILED, PageReason.RATE_LIMITED)
    assert (result.pages[1].status, result.pages[1].reason) == (
        PageStatus.NOT_PROCESSED, PageReason.RUN_STOPPED_AFTER_SYSTEMIC_ERROR)
    assert result.status is RunStatus.FAILED
    usage = db_session.scalars(select(ModelUsage).where(ModelUsage.workspace_id == test_workspace.id)).one()
    assert usage.status == "RATE_LIMITED" and usage.request_id is None and usage.latency_ms >= 0


@pytest.mark.parametrize(
    ("exc", "reason"),
    [(ExtractionTimeoutError("t"), PageReason.TIMEOUT), (ExtractionAPIStatusError("500"), PageReason.API_STATUS_ERROR)],
)
def test_earlier_pages_preserved_when_later_page_hits_api_failure(db_session, test_workspace, exc, reason):
    d = make_doc(db_session, test_workspace, [(PAGE1, [CHUNK_WITH_QUOTE]), ("Page two text here.", ["p2"]), ("Page three text.", ["p3"])])
    fx = FakeExtractor({CHUNK_WITH_QUOTE: ok([fact_payload()]), "p2": exc})
    result = run(db_session, test_workspace, d, fx)
    assert fx.calls == [CHUNK_WITH_QUOTE, "p2"]
    assert [p.status for p in result.pages] == [PageStatus.PROCESSED, PageStatus.FAILED, PageStatus.NOT_PROCESSED]
    assert result.pages[1].reason is reason and result.status is RunStatus.PARTIAL
    assert count(db_session, ExtractedFact, document_id=d.id) == 1  # page 1 kept
    ev = db_session.scalars(select(AuditEvent).where(AuditEvent.entity_id == d.id)).one()
    assert ev.event_type == "document.extraction_partial"


def test_unavailable_extractor_is_failure_not_empty_success(db_session, test_workspace, doc, monkeypatch):
    def no_key():
        raise RuntimeError("MODEL_API_KEY is not configured")

    monkeypatch.setattr("app.ingestion.structured_extractor.get_meta_client", no_key)
    from app.ingestion.structured_extractor import StructuredExtractor

    result = run_document_extraction(
        db_session, workspace_id=test_workspace.id, document_id=doc.id,
        extractor=StructuredExtractor(get_settings()),
    )
    assert result.status is RunStatus.FAILED
    assert result.pages[0].reason is PageReason.EXTRACTOR_UNAVAILABLE
    assert count(db_session, ModelUsage, workspace_id=test_workspace.id) == 0  # no request was made


# ── model usage ───────────────────────────────────────────────────────────────
def test_model_usage_mapping(db_session, test_workspace, doc):
    fx = FakeExtractor({
        CHUNK_WITH_QUOTE: ok(tokens=(123, 45), rid="req-abc", latency=33.3),
        TAIL: ok(tokens=None, rid=None, latency=0.0),
    })
    run(db_session, test_workspace, doc, fx)
    rows = db_session.scalars(
        select(ModelUsage).where(ModelUsage.workspace_id == test_workspace.id).order_by(ModelUsage.created_at, ModelUsage.id)
    ).all()
    assert len(rows) == len(fx.calls) == 2  # exactly one per actual request
    by_rid = {r.request_id: r for r in rows}
    a = by_rid["req-abc"]
    assert (a.provider, a.model_id, a.status) == ("meta", "muse-spark-1.3", "SUCCESS")
    assert (a.input_tokens, a.output_tokens, a.latency_ms) == (123, 45, 33.3)
    b = by_rid[None]
    # schema limitation: token columns are NOT NULL, so unknown usage is stored as 0
    assert (b.input_tokens, b.output_tokens) == (0, 0)
    assert all(r.estimated_cost is None and r.analysis_id is None and r.latency_ms >= 0 for r in rows)


# ── audit ─────────────────────────────────────────────────────────────────────
def test_single_audit_event_with_counts_and_no_source_text(db_session, test_workspace, doc):
    fx = FakeExtractor({CHUNK_WITH_QUOTE: ok([fact_payload()], [req_payload()])})
    result = run(db_session, test_workspace, doc, fx)
    ev = db_session.scalars(select(AuditEvent).where(AuditEvent.workspace_id == test_workspace.id)).one()
    assert (ev.event_type, ev.entity_type, ev.entity_id) == ("document.extraction_completed", "document", doc.id)
    assert ev.request_id == result.extraction_run_id
    m = ev.metadata_
    assert m["extraction_run_id"] == result.extraction_run_id and m["status"] == "COMPLETED"
    assert (m["page_count"], m["pages_processed"], m["pages_skipped"], m["pages_failed"]) == (1, 1, 0, 0)
    assert (m["chunk_requests"], m["model_requests_succeeded"], m["model_requests_failed"]) == (2, 2, 0)
    assert (m["facts_created"], m["facts_reused"], m["requirements_created"], m["requirements_reused"]) == (1, 0, 1, 0)
    assert m["proposals_skipped"] == 0 and m["duration_ms"] >= 0
    assert m["model_id"] == "muse-spark-1.3" and m["prompt_version"] == "v1.1-structured-extraction"
    blob = json.dumps(m)
    assert QUOTE not in blob and "Intro paragraph" not in blob and "MODEL_API_KEY" not in blob


# ── provenance, transactions, protection ─────────────────────────────────────
def test_same_run_id_across_pages_and_new_id_per_run(db_session, test_workspace):
    d = make_doc(db_session, test_workspace, [(PAGE1, [CHUNK_WITH_QUOTE]), (f"Second. {QUOTE} End.", [f"Second. {QUOTE} End."])])
    fx = FakeExtractor({CHUNK_WITH_QUOTE: ok([fact_payload()]), f"Second. {QUOTE} End.": ok([fact_payload(raw="30")])})
    r1 = run(db_session, test_workspace, d, fx)
    facts = db_session.scalars(select(ExtractedFact).where(ExtractedFact.document_id == d.id)).all()
    assert len(facts) == 2
    assert {f.normalized_value["_pramaan"]["extraction_run_id"] for f in facts} == {r1.extraction_run_id}
    r2 = run(db_session, test_workspace, d, fx)
    assert r2.extraction_run_id != r1.extraction_run_id


def test_database_failure_rolls_back_whole_run(db_session, test_workspace, commits, monkeypatch):
    d = make_doc(db_session, test_workspace, [(PAGE1, [CHUNK_WITH_QUOTE]), (f"Second. {QUOTE} End.", [f"Second. {QUOTE} End."])])
    real = orch.persist_page_extraction
    n = {"calls": 0}

    def flaky(session, **kw):
        n["calls"] += 1
        if n["calls"] == 2:
            raise RuntimeError("db exploded")
        return real(session, **kw)

    monkeypatch.setattr(orch, "persist_page_extraction", flaky)
    fx = FakeExtractor({CHUNK_WITH_QUOTE: ok([fact_payload()]), f"Second. {QUOTE} End.": ok([fact_payload(raw="30")])})
    with pytest.raises(ExtractionRunAbortedError):
        run(db_session, test_workspace, d, fx)
    assert commits["rollback"] == 1 and commits["commit"] == 0
    assert n["calls"] == 2  # page 1 really was persisted before the failure, then rolled back
    assert count(db_session, ExtractedFact, document_id=d.id) == 0
    assert count(db_session, AuditEvent, entity_id=d.id) == 0
    assert count(db_session, ModelUsage, workspace_id=test_workspace.id) == 0


def test_document_lock_not_held_during_model_requests(db_session, test_workspace, doc):
    statements: list[str] = []

    def capture(conn, cursor, statement, *a):
        statements.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture)
    locked_during_call: list[bool] = []
    try:
        fx = FakeExtractor(
            {CHUNK_WITH_QUOTE: ok([fact_payload()])},
            on_call=lambda: locked_during_call.append(any("FOR UPDATE" in s for s in statements)),
        )
        run(db_session, test_workspace, doc, fx)
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    assert locked_during_call == [False, False]
    assert any("FOR UPDATE" in s for s in statements)  # taken afterwards, for persistence


def test_human_review_state_survives_rerun(db_session, test_workspace, doc):
    fx = FakeExtractor({CHUNK_WITH_QUOTE: ok([fact_payload()], [req_payload()])})
    run(db_session, test_workspace, doc, fx)
    fact = db_session.scalars(select(ExtractedFact).where(ExtractedFact.document_id == doc.id)).one()
    req = db_session.scalars(select(Requirement).where(Requirement.workspace_id == test_workspace.id)).one()
    link = db_session.scalars(select(EvidenceLink).where(EvidenceLink.fact_id == fact.id)).one()
    fact.verification_status, link.verification_status, req.status = "VERIFIED", "VERIFIED", "ACTIVE"
    db_session.flush()
    again = run(db_session, test_workspace, doc, fx)
    assert (again.facts_created, again.facts_reused, again.requirements_reused) == (0, 1, 1)
    for obj in (fact, link, req):
        db_session.refresh(obj)
    assert (fact.verification_status, link.verification_status, req.status) == ("VERIFIED", "VERIFIED", "ACTIVE")
    assert count(db_session, AuditEvent, entity_id=doc.id) == 2


def test_chunk_regeneration_guard_still_blocks_after_extraction(db_session, test_workspace, doc):
    run(db_session, test_workspace, doc, FakeExtractor({CHUNK_WITH_QUOTE: ok([fact_payload()])}))
    before = count(db_session, DocumentChunk, document_id=doc.id)
    with pytest.raises(EvidenceLinksExistError):
        regenerate_document_chunks(
            session=db_session, settings=get_settings(), workspace_id=test_workspace.id, document_id=doc.id
        )
    assert count(db_session, DocumentChunk, document_id=doc.id) == before


def test_canonical_page_text_never_modified(db_session, test_workspace, doc):
    run(db_session, test_workspace, doc, FakeExtractor({CHUNK_WITH_QUOTE: ok([fact_payload()])}))
    assert DocumentPageRepository(db_session).get_by_page_number(doc.id, 1).extracted_text == PAGE1


def test_ocr_test_isolation_still_pins_tesseract():
    assert get_settings().scanned_page_extractor == "tesseract"


# ── response diagnostics propagation (Milestone 4B.5C.2) ─────────────────────
def _diag(**kw):
    base = {"request_id": "chatcmpl-fail-1", "finish_reason": "length", "input_tokens": 900,
            "output_tokens": 3000, "reasoning_tokens": 2950, "latency_ms": 13000.0}
    base.update(kw)
    return ResponseDiagnostics(**base)


def test_failed_response_usage_uses_real_metadata(db_session, test_workspace):
    d = make_doc(db_session, test_workspace, [("Page one text here.", ["p1"]), ("Page two text here.", ["p2"])])
    fx = FakeExtractor({"p1": ExtractionTruncatedError("cut", _diag())})
    result = run(db_session, test_workspace, d, fx)
    assert result.pages[0].reason is PageReason.RESPONSE_TRUNCATED
    assert result.pages[1].status is PageStatus.PROCESSED  # page-local: the run continues
    rows = {r.request_id: r for r in db_session.scalars(
        select(ModelUsage).where(ModelUsage.workspace_id == test_workspace.id)).all()}
    failed = rows["chatcmpl-fail-1"]
    assert (failed.status, failed.input_tokens, failed.output_tokens, failed.latency_ms) == ("ERROR", 900, 3000, 13000.0)
    assert count(db_session, ModelUsage, workspace_id=test_workspace.id) == len(fx.calls)  # one row per request


def test_unknown_usage_stored_as_zero_but_reported_unknown(db_session, test_workspace, caplog):
    import logging

    caplog.set_level(logging.WARNING)
    d = make_doc(db_session, test_workspace, [(PAGE1, [CHUNK_WITH_QUOTE])])
    unknown = _diag(request_id=None, input_tokens=None, output_tokens=None, reasoning_tokens=None, finish_reason="stop")
    fx = FakeExtractor({CHUNK_WITH_QUOTE: EmptyResponseError("empty", unknown)})
    run(db_session, test_workspace, d, fx)
    row = db_session.scalars(select(ModelUsage).where(ModelUsage.workspace_id == test_workspace.id)).one()
    assert (row.input_tokens, row.output_tokens, row.request_id) == (0, 0, None)  # NOT NULL storage fallback
    rec = next(r for r in caplog.records if getattr(r, "event", "") == "extraction.chunk_failed")
    assert rec.input_tokens is None and rec.output_tokens is None and rec.reasoning_tokens is None
    ev = db_session.scalars(select(AuditEvent).where(AuditEvent.entity_id == d.id)).one()
    assert ev.metadata_["requests_with_unknown_usage"] == 1
    assert ev.metadata_["input_tokens_known"] == 0 and ev.metadata_["output_tokens_known"] == 0


def test_chunk_failed_log_carries_safe_response_metadata(db_session, test_workspace, doc, caplog):
    import logging

    caplog.set_level(logging.WARNING)
    run(db_session, test_workspace, doc, FakeExtractor({CHUNK_WITH_QUOTE: ExtractionTruncatedError("x", _diag())}))
    rec = next(r for r in caplog.records if getattr(r, "event", "") == "extraction.chunk_failed")
    assert (rec.request_id, rec.finish_reason, rec.input_tokens, rec.output_tokens, rec.reasoning_tokens) == (
        "chatcmpl-fail-1", "length", 900, 3000, 2950)
    assert CHUNK_WITH_QUOTE not in caplog.text


def test_audit_aggregates_only_known_usage(db_session, test_workspace, doc):
    fx = FakeExtractor({
        CHUNK_WITH_QUOTE: ok(tokens=(100, 50)),
        TAIL: ExtractionTruncatedError("x", _diag(input_tokens=200, output_tokens=3000, reasoning_tokens=2900)),
    })
    run(db_session, test_workspace, doc, fx)
    m = db_session.scalars(select(AuditEvent).where(AuditEvent.entity_id == doc.id)).one().metadata_
    assert (m["input_tokens_known"], m["output_tokens_known"], m["reasoning_tokens_known"]) == (300, 3050, 2900)
    assert m["requests_with_unknown_usage"] == 0


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (ExtractionTruncatedError("x", _diag()), PageReason.RESPONSE_TRUNCATED),
        (ExtractionRefusalError("x", _diag(finish_reason="stop")), PageReason.MODEL_REFUSAL),
        (ExtractionUnexpectedToolCallsError("x", _diag(finish_reason="tool_calls")), PageReason.UNEXPECTED_TOOL_CALLS),
        (ExtractionUnexpectedFinishReasonError("x", _diag(finish_reason="weird")), PageReason.UNEXPECTED_FINISH_REASON),
    ],
)
def test_new_failure_reasons_in_run_summary_never_persist_candidates(db_session, test_workspace, doc, exc, reason):
    fx = FakeExtractor({CHUNK_WITH_QUOTE: exc})
    result = run(db_session, test_workspace, doc, fx)
    assert result.pages[0].status is PageStatus.FAILED and result.pages[0].reason is reason
    assert result.status is RunStatus.FAILED and result.facts_created == 0
    assert count(db_session, ExtractedFact, document_id=doc.id) == 0
    assert fx.calls == [CHUNK_WITH_QUOTE]  # page-local: this page stops, but no hidden retry
