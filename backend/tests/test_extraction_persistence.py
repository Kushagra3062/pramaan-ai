"""Tests for safe, idempotent extraction persistence (Phase 4B.3B.2). Offline; no Muse Spark."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.repositories.analysis_repo import AnalysisRepository
from app.db.repositories.document_repo import (
    DocumentChunkRepository,
    DocumentPageRepository,
    DocumentRepository,
)
from app.models.document import Document, DocumentChunk
from app.models.enums import (
    DocumentProcessingStatus,
    RequirementStatus,
    VerificationStatus,
)
from app.models.evidence_analysis import EvidenceLink
from app.models.fact_requirement import ExtractedFact, Requirement
from app.models.workspace import Workspace
from app.schemas.extraction import StructuredExtractionResponse
from app.services.document_service import (
    EvidenceLinksExistError,
    regenerate_document_chunks,
)
from app.services.extraction_persistence_service import (
    DocumentNotAvailableError,
    PageNotCanonicalError,
    PageNotFoundError,
    PersistenceProvenance,
    ProposalOutcome,
    SkipReason,
    persist_page_extraction,
)

QUOTE = "The department must submit the report by 30 November."
PAGE1 = f"Intro paragraph. {QUOTE} Closing remarks."
ML = "വകുപ്പ് റിപ്പോർട്ട് സമർപ്പിക്കണം"
PROV = PersistenceProvenance(model_id="muse-spark-1.3", prompt_version="v1.1-structured-extraction")


def make_doc(
    db: Session,
    ws: Workspace,
    pages: list[tuple[str, list[str]]],
    *,
    page_status: str = "COMPLETED",
) -> Document:
    """pages = [(page_text, [chunk contents...]), ...] -> controlled canonical pages/chunks."""
    doc = DocumentRepository(db).create(
        workspace_id=ws.id,
        original_filename="t.pdf",
        storage_key=f"k/{uuid.uuid4()}",
        sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        mime_type="application/pdf",
        file_size_bytes=10,
        page_count=len(pages),
        processing_status=DocumentProcessingStatus.READY.value,
    )
    idx = 0
    for n, (text, chunks) in enumerate(pages, start=1):
        DocumentPageRepository(db).create(
            document_id=doc.id, page_number=n, extracted_text=text, extraction_status=page_status
        )
        for content in chunks:
            DocumentChunkRepository(db).create(
                workspace_id=ws.id,
                document_id=doc.id,
                page_start=n,
                page_end=n,
                chunk_index=idx,
                content=content,
                embedding_model="m",
                embedding_version="1",
                content_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            )
            idx += 1
    return doc


def fact_payload(quote: str = QUOTE, raw: str = "30 November", **kw):
    return {"fact_type": "DEADLINE", "raw_value": raw, "source_quote": quote, **kw}


def req_payload(quote: str = QUOTE, title: str = "Submit report", **kw):
    return {
        "title": title,
        "description": "Department must submit the report.",
        "requirement_type": "REPORTING",
        "source_quote": quote,
        **kw,
    }


def resp(facts=(), reqs=()):
    return StructuredExtractionResponse.model_validate({"facts": list(facts), "requirements": list(reqs)})


def persist(db, ws, doc, response, page=1, prov=PROV):
    return persist_page_extraction(
        db, workspace_id=ws.id, document_id=doc.id, page_number=page, response=response, provenance=prov
    )


def count(db, model, **filters):
    stmt = select(func.count()).select_from(model)
    for k, v in filters.items():
        stmt = stmt.where(getattr(model, k) == v)
    return db.scalar(stmt)


@pytest.fixture
def doc(db_session, test_workspace):
    return make_doc(db_session, test_workspace, [(PAGE1, [f"Intro paragraph. {QUOTE}", "Closing remarks."])])


# ── success + provenance ──────────────────────────────────────────────────────
def test_fact_persisted_with_evidence_and_unreviewed_status(db_session, test_workspace, doc):
    r = persist(db_session, test_workspace, doc, resp(facts=[fact_payload(description="Due date")]))
    assert (r.facts_created, r.evidence_links_created) == (1, 1)
    fact = db_session.get(ExtractedFact, r.results[0].entity_id)
    assert fact.verification_status == VerificationStatus.UNVERIFIED.value
    assert fact.graph_node_id is None
    assert (fact.workspace_id, fact.document_id) == (test_workspace.id, doc.id)
    assert fact.source_quote == QUOTE and fact.raw_value == "30 November"
    meta = fact.normalized_value["_pramaan"]
    assert meta["model_id"] == "muse-spark-1.3" and meta["prompt_version"] == "v1.1-structured-extraction"
    assert meta["page_number"] == 1 and meta["page_id"] == str(fact.page_id)
    assert len(meta["proposal_fingerprint"]) == 64 and meta["extracted_at"]
    assert fact.normalized_value["description"] == "Due date"
    assert "confidence" not in fact.normalized_value and "confidence" not in meta
    link = db_session.get(EvidenceLink, r.results[0].evidence_link_id)
    chunk = db_session.get(DocumentChunk, link.chunk_id)
    assert link.verification_status == VerificationStatus.UNVERIFIED.value
    assert link.fact_id == fact.id and link.requirement_id is None
    assert (chunk.document_id, chunk.workspace_id, chunk.page_start, chunk.page_end) == (
        doc.id,
        test_workspace.id,
        1,
        1,
    )
    assert QUOTE in chunk.content and meta["chunk_id"] == str(chunk.id)


def test_requirement_persisted_as_draft_pending_review(db_session, test_workspace, doc):
    r = persist(db_session, test_workspace, doc, resp(reqs=[req_payload(normalized_value={"modality": "MUST"})]))
    assert (r.requirements_created, r.evidence_links_created) == (1, 1)
    req = db_session.get(Requirement, r.results[0].entity_id)
    assert req.status == RequirementStatus.DRAFT.value and req.graph_node_id is None
    assert req.requirement_type == "REPORTING"
    assert req.normalized_value["_pramaan"]["review_status"] == "PENDING_REVIEW"
    assert req.normalized_value["modality"] == "MUST"
    link = db_session.get(EvidenceLink, r.results[0].evidence_link_id)
    assert link.requirement_id == req.id and link.fact_id is None
    # document provenance is established relationally through the chunk
    assert db_session.get(DocumentChunk, link.chunk_id).document_id == doc.id


def test_malayalam_fact_persisted_verbatim(db_session, test_workspace):
    d = make_doc(db_session, test_workspace, [(f"പ്രമാണം {ML}.", [f"പ്രമാണം {ML}."])])
    r = persist(db_session, test_workspace, d, resp(facts=[fact_payload(quote=ML, raw=ML)]))
    fact = db_session.get(ExtractedFact, r.results[0].entity_id)
    assert fact.source_quote == ML and fact.raw_value == ML


# ── rejected evidence ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "quote",
    ["This sentence is nowhere on the page.", "The department must submit the report by 30 Novemer."],
)
def test_invalid_evidence_not_persisted(db_session, test_workspace, doc, quote):
    r = persist(db_session, test_workspace, doc, resp(facts=[fact_payload(quote)], reqs=[req_payload(quote)]))
    assert [x.outcome for x in r.results] == [ProposalOutcome.SKIPPED] * 2
    assert all(x.skip_reason is SkipReason.QUOTE_NOT_IN_PAGE for x in r.results)
    assert count(db_session, ExtractedFact, document_id=doc.id) == 0
    assert count(db_session, Requirement, workspace_id=test_workspace.id) == 0
    assert count(db_session, EvidenceLink, workspace_id=test_workspace.id) == 0


def test_ambiguous_chunk_match_not_persisted(db_session, test_workspace):
    d = make_doc(db_session, test_workspace, [(PAGE1, [f"A {QUOTE}", f"{QUOTE} B"])])
    r = persist(db_session, test_workspace, d, resp(facts=[fact_payload()], reqs=[req_payload()]))
    assert all(x.skip_reason is SkipReason.AMBIGUOUS_CHUNK_MATCH for x in r.results)
    assert count(db_session, EvidenceLink, workspace_id=test_workspace.id) == 0
    assert count(db_session, ExtractedFact, document_id=d.id) == 0


def test_quote_in_page_but_split_across_chunks_skipped(db_session, test_workspace):
    a, b = "The department must submit", "the report by 30 November."
    d = make_doc(db_session, test_workspace, [(f"{a} {b}", [a, b])])
    r = persist(db_session, test_workspace, d, resp(facts=[fact_payload()]))
    assert r.results[0].skip_reason is SkipReason.QUOTE_IN_PAGE_NO_CHUNK
    assert count(db_session, ExtractedFact, document_id=d.id) == 0


def test_requirement_title_too_long_skipped(db_session, test_workspace, doc):
    r = persist(db_session, test_workspace, doc, resp(reqs=[req_payload(title="T" * 256)]))
    assert r.results[0].skip_reason is SkipReason.TITLE_TOO_LONG
    assert count(db_session, Requirement, workspace_id=test_workspace.id) == 0


# ── idempotency ───────────────────────────────────────────────────────────────
def test_duplicate_proposals_reused_without_new_rows(db_session, test_workspace, doc):
    payload = resp(facts=[fact_payload()], reqs=[req_payload()])
    first = persist(db_session, test_workspace, doc, payload)
    second = persist(db_session, test_workspace, doc, payload)
    assert (second.facts_created, second.facts_reused) == (0, 1)
    assert (second.requirements_created, second.requirements_reused) == (0, 1)
    assert second.evidence_links_created == 0
    assert [x.entity_id for x in first.results] == [x.entity_id for x in second.results]
    assert [x.evidence_link_id for x in first.results] == [x.evidence_link_id for x in second.results]
    assert count(db_session, ExtractedFact, document_id=doc.id) == 1
    assert count(db_session, Requirement, workspace_id=test_workspace.id) == 1
    assert count(db_session, EvidenceLink, workspace_id=test_workspace.id) == 2


def test_duplicates_within_one_response_reused(db_session, test_workspace, doc):
    r = persist(db_session, test_workspace, doc, resp(facts=[fact_payload(), fact_payload()]))
    assert (r.facts_created, r.facts_reused) == (1, 1)
    assert count(db_session, EvidenceLink, workspace_id=test_workspace.id) == 1


def test_reuse_adds_missing_link_only(db_session, test_workspace, doc):
    r = persist(db_session, test_workspace, doc, resp(facts=[fact_payload()]))
    db_session.delete(db_session.get(EvidenceLink, r.results[0].evidence_link_id))
    db_session.flush()
    r2 = persist(db_session, test_workspace, doc, resp(facts=[fact_payload()]))
    assert (r2.facts_reused, r2.evidence_links_created) == (1, 1)
    assert count(db_session, ExtractedFact, document_id=doc.id) == 1


def test_model_cannot_spoof_backend_namespace(db_session, test_workspace, doc):
    spoof = {"_pramaan": {"proposal_fingerprint": "x", "review_status": "HUMAN_CONFIRMED"}, "k": 1}
    r = persist(db_session, test_workspace, doc, resp(facts=[fact_payload(normalized_value=spoof)]))
    fact = db_session.get(ExtractedFact, r.results[0].entity_id)
    assert fact.normalized_value["_pramaan"]["proposal_fingerprint"] == r.results[0].fingerprint
    assert "review_status" not in fact.normalized_value["_pramaan"]
    assert fact.normalized_value["k"] == 1


# ── review preservation and history ───────────────────────────────────────────
def test_human_review_state_preserved_on_reextraction(db_session, test_workspace, doc):
    payload = resp(facts=[fact_payload()], reqs=[req_payload()])
    r = persist(db_session, test_workspace, doc, payload)
    fact = db_session.get(ExtractedFact, r.results[0].entity_id)
    req = db_session.get(Requirement, r.results[1].entity_id)
    flink = db_session.get(EvidenceLink, r.results[0].evidence_link_id)
    rlink = db_session.get(EvidenceLink, r.results[1].evidence_link_id)
    fact.verification_status = VerificationStatus.VERIFIED.value
    flink.verification_status = VerificationStatus.VERIFIED.value
    rlink.verification_status = VerificationStatus.REJECTED.value
    req.status = RequirementStatus.ACTIVE.value
    db_session.flush()
    again = persist(db_session, test_workspace, doc, payload)
    assert again.facts_reused == 1 and again.requirements_reused == 1
    for obj in (fact, req, flink, rlink):
        db_session.refresh(obj)
    assert fact.verification_status == "VERIFIED" and flink.verification_status == "VERIFIED"
    assert rlink.verification_status == "REJECTED" and req.status == "ACTIVE"
    assert count(db_session, EvidenceLink, workspace_id=test_workspace.id) == 2


def test_changed_proposal_keeps_history(db_session, test_workspace, doc):
    first = persist(db_session, test_workspace, doc, resp(facts=[fact_payload(raw="30 Nov")]))
    second = persist(db_session, test_workspace, doc, resp(facts=[fact_payload(raw="30 November")]))
    assert second.facts_created == 1
    assert first.results[0].entity_id != second.results[0].entity_id
    assert count(db_session, ExtractedFact, document_id=doc.id) == 2
    old = db_session.get(ExtractedFact, first.results[0].entity_id)
    assert old.raw_value == "30 Nov" and old.verification_status == "UNVERIFIED"


def test_model_version_change_does_not_duplicate(db_session, test_workspace, doc):
    persist(db_session, test_workspace, doc, resp(facts=[fact_payload()]))
    other = PersistenceProvenance(model_id="other-model", prompt_version="v2")
    r = persist(db_session, test_workspace, doc, resp(facts=[fact_payload()]), prov=other)
    assert r.facts_reused == 1


# ── transactions ──────────────────────────────────────────────────────────────
def test_failure_rolls_back_call_but_not_outer_work(db_session, test_workspace, doc, monkeypatch):
    outer = ExtractedFact(
        workspace_id=test_workspace.id,
        document_id=doc.id,
        fact_type="DATE",
        raw_value="x",
        source_quote="x",
        extraction_method="m",
    )
    db_session.add(outer)
    db_session.flush()
    outer_id = outer.id

    original = AnalysisRepository.add_evidence_link
    calls = {"n": 0}

    def flaky(self, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return original(self, **kwargs)

    monkeypatch.setattr(AnalysisRepository, "add_evidence_link", flaky)
    with pytest.raises(RuntimeError):
        persist(db_session, test_workspace, doc, resp(facts=[fact_payload(), fact_payload(raw="November")]))
    assert count(db_session, ExtractedFact, document_id=doc.id) == 1  # only the outer fact
    assert count(db_session, EvidenceLink, workspace_id=test_workspace.id) == 0
    assert db_session.get(ExtractedFact, outer_id) is not None


def test_service_never_commits(db_session, test_workspace, doc, monkeypatch):
    def no_commit(*a, **k):
        raise AssertionError("service must not commit")

    monkeypatch.setattr(db_session, "commit", no_commit)
    persist(db_session, test_workspace, doc, resp(facts=[fact_payload()], reqs=[req_payload()]))


# ── isolation and provenance ──────────────────────────────────────────────────
def test_workspace_isolation(db_session, test_workspace, doc):
    other = Workspace(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
    db_session.add(other)
    db_session.flush()
    with pytest.raises(DocumentNotAvailableError):
        persist_page_extraction(
            db_session,
            workspace_id=other.id,
            document_id=doc.id,
            page_number=1,
            response=resp(facts=[fact_payload()]),
            provenance=PROV,
        )
    assert count(db_session, ExtractedFact, document_id=doc.id) == 0


def test_chunks_of_other_document_never_used(db_session, test_workspace):
    a = make_doc(db_session, test_workspace, [(PAGE1, [])])  # page text but no chunks
    make_doc(db_session, test_workspace, [(PAGE1, [PAGE1])])  # same text, other document
    r = persist(db_session, test_workspace, a, resp(facts=[fact_payload()]))
    assert r.results[0].skip_reason is SkipReason.QUOTE_IN_PAGE_NO_CHUNK
    assert count(db_session, EvidenceLink, workspace_id=test_workspace.id) == 0


def test_chunk_from_other_page_never_used(db_session, test_workspace):
    d = make_doc(db_session, test_workspace, [(PAGE1, ["Closing remarks."]), ("Other page.", [QUOTE])])
    r = persist(db_session, test_workspace, d, resp(facts=[fact_payload()]), page=1)
    assert r.results[0].skip_reason is SkipReason.QUOTE_IN_PAGE_NO_CHUNK


def test_unavailable_inputs_raise(db_session, test_workspace, doc):
    with pytest.raises(PageNotFoundError):
        persist(db_session, test_workspace, doc, resp(), page=9)
    bad = make_doc(db_session, test_workspace, [(PAGE1, [PAGE1])], page_status="NEEDS_OCR")
    with pytest.raises(PageNotCanonicalError):
        persist(db_session, test_workspace, bad, resp(facts=[fact_payload()]))
    empty = make_doc(db_session, test_workspace, [("", [])])
    with pytest.raises(PageNotCanonicalError):
        persist(db_session, test_workspace, empty, resp())
    doc.processing_status = DocumentProcessingStatus.DELETING.value
    db_session.flush()
    with pytest.raises(DocumentNotAvailableError):
        persist(db_session, test_workspace, doc, resp())


def test_empty_response_is_noop(db_session, test_workspace, doc):
    assert persist(db_session, test_workspace, doc, resp()).results == []


# ── chunk-regeneration guard ──────────────────────────────────────────────────
def test_chunk_regeneration_refused_when_evidence_exists(db_session, test_workspace, doc):
    persist(db_session, test_workspace, doc, resp(facts=[fact_payload()], reqs=[req_payload()]))
    chunk_ids = {c.id for c in DocumentChunkRepository(db_session).list_by_document(doc.id)}
    with pytest.raises(EvidenceLinksExistError):
        regenerate_document_chunks(
            session=db_session, settings=get_settings(), workspace_id=test_workspace.id, document_id=doc.id
        )
    assert {c.id for c in DocumentChunkRepository(db_session).list_by_document(doc.id)} == chunk_ids
    assert count(db_session, EvidenceLink, workspace_id=test_workspace.id) == 2


def test_chunk_regeneration_allowed_without_evidence(db_session, test_workspace, doc):
    n = regenerate_document_chunks(
        session=db_session, settings=get_settings(), workspace_id=test_workspace.id, document_id=doc.id
    )
    assert n >= 1


# ── concurrency (structural: true two-connection races are not exercised here) ─
def test_document_row_locked_before_any_lookup(db_session, test_workspace, doc):
    statements: list[str] = []

    def capture(conn, cursor, statement, *a):
        statements.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture)
    try:
        persist(db_session, test_workspace, doc, resp(facts=[fact_payload()]))
    finally:
        event.remove(bind, "before_cursor_execute", capture)
    lock_idx = next(i for i, s in enumerate(statements) if "FROM documents" in s and "FOR UPDATE" in s)
    fact_lookup = next(i for i, s in enumerate(statements) if "FROM extracted_facts" in s)
    assert lock_idx < fact_lookup


# ── fact raw_value must appear in its own source_quote ────────────────────────
def test_fact_value_contained_in_quote_is_accepted(db_session, test_workspace, doc):
    r = persist(db_session, test_workspace, doc, resp(facts=[fact_payload(raw="30 November")]))
    assert r.results[0].outcome is ProposalOutcome.CREATED


def test_fact_value_absent_from_quote_is_skipped_without_writes(db_session, test_workspace, doc):
    r = persist(db_session, test_workspace, doc, resp(facts=[fact_payload(raw="15 December")]))
    assert r.results[0].outcome is ProposalOutcome.SKIPPED
    assert r.results[0].skip_reason is SkipReason.FACT_VALUE_NOT_IN_EVIDENCE
    assert r.results[0].entity_id is None and r.results[0].evidence_link_id is None
    assert count(db_session, ExtractedFact, document_id=doc.id) == 0
    assert count(db_session, EvidenceLink, workspace_id=test_workspace.id) == 0


def test_malayalam_fact_value_inside_malayalam_evidence(db_session, test_workspace):
    quote = f"പ്രമാണം {ML}"
    d = make_doc(db_session, test_workspace, [(f"{quote}.", [f"{quote}."])])
    ok = persist(db_session, test_workspace, d, resp(facts=[fact_payload(quote=quote, raw=ML)]))
    assert ok.results[0].outcome is ProposalOutcome.CREATED
    bad = persist(db_session, test_workspace, d, resp(facts=[fact_payload(quote=quote, raw="മറ്റൊന്ന്")]))
    assert bad.results[0].skip_reason is SkipReason.FACT_VALUE_NOT_IN_EVIDENCE


def test_fact_value_whitespace_equivalence(db_session, test_workspace, doc):
    r = persist(db_session, test_workspace, doc, resp(facts=[fact_payload(raw="30   \n November")]))
    assert r.results[0].outcome is ProposalOutcome.CREATED


def test_fact_value_nfc_equivalence(db_session, test_workspace):
    import unicodedata

    composed = "Café Fund"
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed
    d = make_doc(db_session, test_workspace, [(f"Payee: {composed} account.", [f"Payee: {composed} account."])])
    r = persist(
        db_session,
        test_workspace,
        d,
        resp(facts=[fact_payload(quote=f"Payee: {composed} account.", raw=decomposed)]),
    )
    assert r.results[0].outcome is ProposalOutcome.CREATED


def test_fact_value_case_mismatch_rejected(db_session, test_workspace, doc):
    r = persist(db_session, test_workspace, doc, resp(facts=[fact_payload(raw="30 november")]))
    assert r.results[0].skip_reason is SkipReason.FACT_VALUE_NOT_IN_EVIDENCE
    assert count(db_session, ExtractedFact, document_id=doc.id) == 0


def test_requirement_title_need_not_appear_in_quote(db_session, test_workspace, doc):
    r = persist(db_session, test_workspace, doc, resp(reqs=[req_payload(title="Annual compliance filing")]))
    assert r.results[0].outcome is ProposalOutcome.CREATED
    assert count(db_session, Requirement, workspace_id=test_workspace.id) == 1


# ── canonical text consistency with the real chunker ──────────────────────────
def test_persistence_matches_real_chunker_output_and_leaves_page_text_intact(db_session, test_workspace):
    from app.services.document_service import generate_and_persist_canonical_chunks

    raw_page = f"[OCR EXTRACTED]: Heading line\n\n\n\n{QUOTE}\nMalayalam: {ML}\n"
    d = make_doc(db_session, test_workspace, [(raw_page, [])])
    generate_and_persist_canonical_chunks(
        session=db_session, settings=get_settings(), workspace_id=test_workspace.id, document_id=d.id
    )
    r = persist(
        db_session,
        test_workspace,
        d,
        resp(facts=[fact_payload(), fact_payload(quote=ML, raw=ML)], reqs=[req_payload()]),
    )
    assert [x.outcome for x in r.results] == [ProposalOutcome.CREATED] * 3
    page = DocumentPageRepository(db_session).get_by_page_number(d.id, 1)
    assert page.extracted_text == raw_page  # canonical page text never rewritten
    ml_chunk = db_session.get(DocumentChunk, r.results[1].chunk_id)
    assert ML in ml_chunk.content  # Malayalam intact through chunking
