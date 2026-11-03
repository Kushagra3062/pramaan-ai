"""API tests for extraction, fact retrieval and requirement retrieval (Milestone 4B.4B).

The extractor dependency is always replaced by a scripted fake. There is no authentication in
the application yet, so there are deliberately no "unauthorized access" tests.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.routes.extraction import get_structured_extractor
from app.core.config import get_settings
from app.db.repositories.analysis_repo import AnalysisRepository
from app.db.repositories.document_repo import DocumentChunkRepository, DocumentPageRepository
from app.ingestion.structured_extractor import (
    ExtractionAPIError,
    ExtractionAPIStatusError,
    ExtractionConnectionError,
    ExtractionJSONParseError,
    ExtractionRateLimitError,
    ExtractionRefusalError,
    ExtractionTimeoutError,
    ExtractionTruncatedError,
    ExtractionUnexpectedFinishReasonError,
    ExtractionUnexpectedToolCallsError,
)
from app.main import app
from app.models.document import DocumentChunk
from app.models.enums import DocumentProcessingStatus
from app.models.evidence_analysis import EvidenceLink
from app.models.fact_requirement import ExtractedFact, Requirement
from app.models.workspace import Workspace
from app.services.document_service import upload_document
from app.services.extraction_orchestrator import ExtractionRunAbortedError
from tests.helpers import create_synthetic_pdf
from tests.test_extraction_orchestrator import FakeExtractor, ok
from tests.test_extraction_persistence import (
    ML,
    PAGE1,
    PROV,
    QUOTE,
    fact_payload,
    make_doc,
    persist,
    req_payload,
    resp,
)

CHUNK_A = f"Intro paragraph. {QUOTE}"
TAIL = "Closing remarks."
Q2 = "The board shall approve the budget."


@pytest.fixture(autouse=True)
def _no_real_meta_client(monkeypatch):
    def boom():
        raise AssertionError("real Meta client must never be used in tests")

    monkeypatch.setattr("app.ingestion.structured_extractor.get_meta_client", boom)


def base(ws, doc) -> str:
    return f"/api/v1/workspaces/{ws.id}/documents/{doc.id}"


def use_extractor(fx) -> None:
    app.dependency_overrides[get_structured_extractor] = lambda: fx


def use_cap(n: int | None = None, **updates) -> None:
    if n is not None:
        updates["max_model_calls_per_request"] = n
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(update=updates)


def count(db, model, **filters):
    stmt = select(func.count()).select_from(model)
    for k, v in filters.items():
        stmt = stmt.where(getattr(model, k) == v)
    return db.scalar(stmt)


@pytest.fixture
def doc(db_session, test_workspace):
    return make_doc(db_session, test_workspace, [(PAGE1, [CHUNK_A, TAIL])])


def other_workspace(db):
    ws = Workspace(name="Other", slug=f"o-{uuid.uuid4().hex[:8]}")
    db.add(ws)
    db.flush()
    return ws


# ══ A. extraction endpoint ═══════════════════════════════════════════════════
def test_extraction_completed(client: TestClient, db_session, test_workspace, doc):
    fx = FakeExtractor({CHUNK_A: ok([fact_payload()], [req_payload()])})
    use_extractor(fx)
    r = client.post(f"{base(test_workspace, doc)}/extract")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "COMPLETED" and body["document_id"] == str(doc.id)
    assert body["extraction_run_id"] and body["pages"][0]["status"] == "PROCESSED"
    assert (body["facts_created"], body["requirements_created"], body["chunk_requests"]) == (1, 1, 2)
    assert fx.calls == [CHUNK_A, TAIL]


def test_partial_extraction_is_200(client, db_session, test_workspace):
    d = make_doc(db_session, test_workspace, [(PAGE1, [CHUNK_A]), ("Second page text here.", ["p2"])])
    use_extractor(FakeExtractor({"p2": ExtractionJSONParseError("bad")}))
    r = client.post(f"{base(test_workspace, d)}/extract")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "PARTIAL" and body["pages_failed"] == 1
    assert body["pages"][1]["reason"] == "INVALID_JSON"


def test_missing_wrong_workspace_and_deleted_document_404(client, db_session, test_workspace, doc):
    use_extractor(FakeExtractor())
    assert client.post(f"/api/v1/workspaces/{test_workspace.id}/documents/{uuid.uuid4()}/extract").status_code == 404
    ws2 = other_workspace(db_session)
    assert client.post(f"/api/v1/workspaces/{ws2.id}/documents/{doc.id}/extract").status_code == 404
    doc.deleted_at = datetime.now(UTC)
    db_session.flush()
    assert client.post(f"{base(test_workspace, doc)}/extract").status_code == 404
    doc.deleted_at = None
    doc.processing_status = DocumentProcessingStatus.DELETING.value
    db_session.flush()
    assert client.post(f"{base(test_workspace, doc)}/extract").status_code == 404


@pytest.mark.parametrize("state", ["NEEDS_OCR", "PROCESSING", "FAILED", "UPLOADED"])
def test_non_ready_document_409_without_model_calls(client, db_session, test_workspace, doc, state):
    doc.processing_status = state
    db_session.flush()
    fx = FakeExtractor()
    use_extractor(fx)
    assert client.post(f"{base(test_workspace, doc)}/extract").status_code == 409
    assert fx.calls == []


def test_no_eligible_chunks_409(client, db_session, test_workspace):
    d = make_doc(db_session, test_workspace, [(PAGE1, [])])
    fx = FakeExtractor()
    use_extractor(fx)
    r = client.post(f"{base(test_workspace, d)}/extract")
    assert r.status_code == 409 and fx.calls == []


def test_exactly_max_calls_allowed(client, db_session, test_workspace):
    d = make_doc(db_session, test_workspace, [(PAGE1, [f"chunk {i}" for i in range(4)])])
    fx = FakeExtractor()
    use_extractor(fx)
    use_cap(4)
    r = client.post(f"{base(test_workspace, d)}/extract")
    assert r.status_code == 200 and len(fx.calls) == 4  # parity: preflight count == real calls


def test_one_over_max_calls_413_and_zero_calls(client, db_session, test_workspace):
    d = make_doc(db_session, test_workspace, [(PAGE1, [f"chunk {i}" for i in range(5)])])
    fx = FakeExtractor()
    use_extractor(fx)
    use_cap(4)
    r = client.post(f"{base(test_workspace, d)}/extract")
    assert r.status_code == 413 and fx.calls == []
    assert count(db_session, ExtractedFact, document_id=d.id) == 0
    assert "chunk 0" not in r.text


def test_cap_counts_only_eligible_chunks_not_pages(client, db_session, test_workspace):
    d = make_doc(db_session, test_workspace, [(PAGE1, ["a", "b"]), ("", ["ignored blank page chunk"])])
    fx = FakeExtractor()
    use_extractor(fx)
    use_cap(2)
    assert client.post(f"{base(test_workspace, d)}/extract").status_code == 200
    assert fx.calls == ["a", "b"]


def test_missing_api_key_default_extractor_503(client, db_session, test_workspace, doc):
    use_cap(model_api_key=None)  # default extractor dependency is NOT overridden
    r = client.post(f"{base(test_workspace, doc)}/extract")
    assert r.status_code == 503
    assert "MODEL_API_KEY" not in r.text


@pytest.mark.parametrize(
    ("exc", "code", "reason"),
    [
        (ExtractionRateLimitError("slow"), 429, "RATE_LIMITED"),
        (ExtractionTimeoutError("t"), 504, "TIMEOUT"),
        (ExtractionConnectionError("c"), 502, "CONNECTION_ERROR"),
        (ExtractionAPIStatusError("500"), 502, "API_STATUS_ERROR"),
        (ExtractionAPIError("x"), 502, "API_ERROR"),
        (ExtractionJSONParseError("j"), 502, "INVALID_JSON"),
    ],
)
def test_failed_runs_map_status_and_keep_full_body(client, db_session, test_workspace, doc, exc, code, reason):
    use_extractor(FakeExtractor({CHUNK_A: exc}))
    r = client.post(f"{base(test_workspace, doc)}/extract")
    assert r.status_code == code
    body = r.json()
    assert body["status"] == "FAILED" and body["extraction_run_id"] and body["document_id"] == str(doc.id)
    assert body["pages"][0]["status"] == "FAILED" and body["pages"][0]["reason"] == reason
    assert body["model_requests_failed"] == 1 and "detail" not in body
    assert QUOTE not in r.text and CHUNK_A not in r.text


def test_unavailable_extractor_503_with_body(client, db_session, test_workspace, doc):
    class Broken(FakeExtractor):
        def extract_from_text(self, source_text):
            raise RuntimeError("MODEL_API_KEY is not configured")

    use_extractor(Broken())
    r = client.post(f"{base(test_workspace, doc)}/extract")
    assert r.status_code == 503 and r.json()["pages"][0]["reason"] == "EXTRACTOR_UNAVAILABLE"
    assert "MODEL_API_KEY" not in r.text


def test_failure_precedence_is_deterministic(client, db_session, test_workspace):
    d = make_doc(db_session, test_workspace, [("Page one text here.", ["p1"]), ("Page two text here.", ["p2"])])
    use_extractor(FakeExtractor({"p1": ExtractionJSONParseError("j"), "p2": ExtractionTimeoutError("t")}))
    r = client.post(f"{base(test_workspace, d)}/extract")
    assert r.status_code == 504  # TIMEOUT outranks INVALID_JSON


def test_persistence_abort_is_safe_500(client, db_session, test_workspace, doc, monkeypatch):
    def abort(*a, **k):
        raise ExtractionRunAbortedError("db exploded: SELECT secret FROM x")

    monkeypatch.setattr("app.api.routes.extraction.run_document_extraction", abort)
    use_extractor(FakeExtractor())
    r = client.post(f"{base(test_workspace, doc)}/extract")
    assert r.status_code == 500
    assert "SELECT" not in r.text and "exploded" not in r.text and QUOTE not in r.text


def test_extract_takes_no_body_or_ids(client, db_session, test_workspace, doc):
    use_extractor(FakeExtractor())
    body = client.post(f"{base(test_workspace, doc)}/extract", json={"fact_id": str(uuid.uuid4()), "model_id": "x"})
    assert body.status_code == 200  # unknown body is ignored; nothing in it is used


# ══ B. fact retrieval ════════════════════════════════════════════════════════
def test_fact_listing_with_evidence(client, db_session, test_workspace, doc):
    persist(db_session, test_workspace, doc, resp(facts=[fact_payload(normalized_value={"k": 1})]))
    r = client.get(f"{base(test_workspace, doc)}/facts")
    assert r.status_code == 200
    body = r.json()
    assert (body["total"], body["limit"], body["offset"]) == (1, 20, 0)
    f = body["items"][0]
    assert f["fact_type"] == "DEADLINE" and f["raw_value"] == "30 November" and f["source_quote"] == QUOTE
    assert f["page_number"] == 1 and f["verification_status"] == "UNVERIFIED"
    assert f["extraction_method"] == "LLM_STRUCTURED_EXTRACTION" and f["created_at"] and f["updated_at"]
    assert f["normalized_value"] == {"k": 1}
    (ev,) = f["evidence"]
    assert set(ev) == {"evidence_link_id", "chunk_id", "chunk_index", "page_number", "relationship_type", "verification_status"}
    assert (ev["chunk_index"], ev["page_number"], ev["relationship_type"], ev["verification_status"]) == (0, 1, "SUPPORTS", "UNVERIFIED")


def test_empty_fact_list(client, db_session, test_workspace, doc):
    body = client.get(f"{base(test_workspace, doc)}/facts").json()
    assert body == {"items": [], "total": 0, "limit": 20, "offset": 0}


def test_fact_pagination_and_filters(client, db_session, test_workspace, doc):
    persist(
        db_session,
        test_workspace,
        doc,
        resp(
            facts=[
                fact_payload(raw="30"),
                fact_payload(raw="November"),
                {"fact_type": "DATE", "raw_value": "30 November", "source_quote": QUOTE},
            ]
        ),
    )
    url = f"{base(test_workspace, doc)}/facts"
    page1 = client.get(url, params={"limit": 2, "offset": 0}).json()
    page2 = client.get(url, params={"limit": 2, "offset": 2}).json()
    assert (page1["total"], len(page1["items"]), len(page2["items"])) == (3, 2, 1)
    ids = [i["fact_id"] for i in page1["items"] + page2["items"]]
    assert len(set(ids)) == 3  # no overlap between pages
    by_type = client.get(url, params={"fact_type": "DATE"}).json()
    assert by_type["total"] == 1 and by_type["items"][0]["fact_type"] == "DATE"
    fact = db_session.get(ExtractedFact, ids[0])
    fact.verification_status = "VERIFIED"
    db_session.flush()
    verified = client.get(url, params={"verification_status": "VERIFIED"}).json()
    assert verified["total"] == 1 and verified["items"][0]["fact_id"] == ids[0]
    assert client.get(url, params={"fact_type": "NOPE"}).status_code == 422
    assert client.get(url, params={"limit": 0}).status_code == 422
    assert client.get(url, params={"limit": 101}).status_code == 422
    assert client.get(url, params={"offset": -1}).status_code == 422


def test_fact_ordering_and_null_page(client, db_session, test_workspace):
    d = make_doc(
        db_session,
        test_workspace,
        [(f"Alpha {QUOTE}", [f"Alpha {QUOTE}"]), (f"Beta {Q2}", [f"Beta {Q2}"]), (f"Gamma {QUOTE}", [f"Gamma {QUOTE}"])],
    )
    persist(db_session, test_workspace, d, resp(facts=[fact_payload(quote=Q2, raw="approve")]), page=2)
    persist(db_session, test_workspace, d, resp(facts=[fact_payload()]), page=1)
    persist(db_session, test_workspace, d, resp(facts=[fact_payload(raw="30")]), page=3)
    items = client.get(f"{base(test_workspace, d)}/facts").json()["items"]
    assert [i["page_number"] for i in items] == [1, 2, 3]
    orphan = db_session.get(ExtractedFact, items[0]["fact_id"])
    orphan.page_id = None  # page link lost (ON DELETE SET NULL scenario)
    db_session.flush()
    items = client.get(f"{base(test_workspace, d)}/facts").json()["items"]
    assert [i["page_number"] for i in items] == [2, 3, None]  # NULLS LAST, row preserved
    assert len(items[-1]["evidence"]) == 1


def test_facts_scoped_to_document_and_workspace(client, db_session, test_workspace, doc):
    other_doc = make_doc(db_session, test_workspace, [(PAGE1, [CHUNK_A])])
    persist(db_session, test_workspace, doc, resp(facts=[fact_payload()]))
    persist(db_session, test_workspace, other_doc, resp(facts=[fact_payload(raw="30")]))
    ws2 = other_workspace(db_session)
    foreign = ExtractedFact(  # inconsistent data: other workspace, same document id
        workspace_id=ws2.id, document_id=doc.id, fact_type="DATE", raw_value="x", source_quote="x", extraction_method="m"
    )
    db_session.add(foreign)
    db_session.flush()
    body = client.get(f"{base(test_workspace, doc)}/facts").json()
    assert body["total"] == 1 and body["items"][0]["raw_value"] == "30 November"
    assert client.get(f"/api/v1/workspaces/{ws2.id}/documents/{doc.id}/facts").status_code == 404
    doc.deleted_at = datetime.now(UTC)
    db_session.flush()
    assert client.get(f"{base(test_workspace, doc)}/facts").status_code == 404


def test_fact_provenance_sanitised(client, db_session, test_workspace, doc):
    spoof = {"_pramaan": {"proposal_fingerprint": "evil"}, "keep": "me"}
    r = persist(db_session, test_workspace, doc, resp(facts=[fact_payload(normalized_value=spoof)]))
    resp_ = client.get(f"{base(test_workspace, doc)}/facts")
    f = resp_.json()["items"][0]
    assert "_pramaan" not in f["normalized_value"] and f["normalized_value"] == {"keep": "me"}
    prov = f["provenance"]
    assert set(prov) == {
        "extraction_run_id", "model_id", "prompt_version", "extraction_method", "extracted_at", "page_number", "review_status"
    }
    assert prov["model_id"] == PROV.model_id and prov["prompt_version"] == PROV.prompt_version
    assert prov["page_number"] == 1 and prov["review_status"] is None
    assert "proposal_fingerprint" not in resp_.text and r.results[0].fingerprint not in resp_.text
    stored = db_session.get(ExtractedFact, f["fact_id"])
    assert "_pramaan" in stored.normalized_value  # serialization did not mutate storage


def test_fact_with_only_provenance_has_null_model_metadata(client, db_session, test_workspace, doc):
    persist(db_session, test_workspace, doc, resp(facts=[fact_payload()]))
    assert client.get(f"{base(test_workspace, doc)}/facts").json()["items"][0]["normalized_value"] is None


def test_malayalam_facts_round_trip(client, db_session, test_workspace):
    d = make_doc(db_session, test_workspace, [(f"പ്രമാണം {ML}.", [f"പ്രമാണം {ML}."])])
    persist(db_session, test_workspace, d, resp(facts=[fact_payload(quote=ML, raw=ML, normalized_value={"മൂല്യം": ML})]))
    r = client.get(f"{base(test_workspace, d)}/facts")
    f = r.json()["items"][0]
    assert f["raw_value"] == ML and f["source_quote"] == ML and f["normalized_value"] == {"മൂല്യം": ML}
    assert ML in r.content.decode("utf-8")  # emitted as real UTF-8, not lossy


# ══ C. requirement retrieval ═════════════════════════════════════════════════
def test_requirement_listing(client, db_session, test_workspace, doc):
    persist(db_session, test_workspace, doc, resp(reqs=[req_payload(normalized_value={"modality": "MUST"})]))
    r = client.get(f"{base(test_workspace, doc)}/requirements")
    assert r.status_code == 200
    body = r.json()
    assert (body["total"], body["limit"], body["offset"]) == (1, 20, 0)
    q = body["items"][0]
    assert (q["title"], q["requirement_type"], q["status"]) == ("Submit report", "REPORTING", "DRAFT")
    assert q["review_status"] == "PENDING_REVIEW" and q["source_quote"] == QUOTE
    assert q["normalized_value"]["modality"] == "MUST" and "_pramaan" not in q["normalized_value"]
    assert q["provenance"]["review_status"] == "PENDING_REVIEW" and q["provenance"]["model_id"] == PROV.model_id
    (ev,) = q["evidence"]
    assert (ev["chunk_index"], ev["page_number"], ev["verification_status"]) == (0, 1, "UNVERIFIED")


def test_empty_requirements(client, db_session, test_workspace, doc):
    assert client.get(f"{base(test_workspace, doc)}/requirements").json() == {
        "items": [], "total": 0, "limit": 20, "offset": 0}


def test_requirement_pagination_and_status_filter(client, db_session, test_workspace, doc):
    persist(
        db_session, test_workspace, doc,
        resp(reqs=[req_payload(title="One"), req_payload(title="Two"), req_payload(title="Three")]),
    )
    url = f"{base(test_workspace, doc)}/requirements"
    a = client.get(url, params={"limit": 2}).json()
    b = client.get(url, params={"limit": 2, "offset": 2}).json()
    assert (a["total"], len(a["items"]), len(b["items"])) == (3, 2, 1)
    ordered = a["items"] + b["items"]
    assert sorted(i["title"] for i in ordered) == ["One", "Three", "Two"]  # disjoint pages, nothing lost
    # documented order is (created_at, id); rows from one run share created_at, so id breaks ties
    keys = [(i["created_at"], i["requirement_id"]) for i in ordered]
    assert keys == sorted(keys)
    assert [i["requirement_id"] for i in client.get(url, params={"limit": 3}).json()["items"]] == [
        i["requirement_id"] for i in ordered
    ]  # stable across calls
    req = db_session.get(Requirement, a["items"][0]["requirement_id"])
    req.status = "ACTIVE"
    db_session.flush()
    active = client.get(url, params={"status": "ACTIVE"}).json()
    assert active["total"] == 1 and active["items"][0]["requirement_id"] == str(req.id)
    assert client.get(url, params={"status": "BOGUS"}).status_code == 422


def test_requirement_with_many_links_appears_once(client, db_session, test_workspace, doc):
    r = persist(db_session, test_workspace, doc, resp(reqs=[req_payload()]))
    req_id = r.results[0].entity_id
    chunks = DocumentChunkRepository(db_session).list_by_document(doc.id)
    AnalysisRepository(db_session).add_evidence_link(
        workspace_id=test_workspace.id, chunk_id=chunks[1].id, requirement_id=req_id
    )
    body = client.get(f"{base(test_workspace, doc)}/requirements").json()
    assert body["total"] == 1 and len(body["items"]) == 1  # not inflated by links
    assert [e["chunk_index"] for e in body["items"][0]["evidence"]] == [0, 1]
    page = client.get(f"{base(test_workspace, doc)}/requirements", params={"limit": 1}).json()
    assert page["total"] == 1 and len(page["items"]) == 1


def test_requirements_scoped_to_document_and_workspace(client, db_session, test_workspace, doc):
    other_doc = make_doc(db_session, test_workspace, [(PAGE1, [CHUNK_A])])
    only_other = persist(db_session, test_workspace, other_doc, resp(reqs=[req_payload(title="Other doc only")]))
    mine = persist(db_session, test_workspace, doc, resp(reqs=[req_payload(title="Mine")]))
    # "Mine" also gains evidence in the other document: listing for `doc` must show only doc's evidence
    other_chunk = DocumentChunkRepository(db_session).list_by_document(other_doc.id)[0]
    AnalysisRepository(db_session).add_evidence_link(
        workspace_id=test_workspace.id, chunk_id=other_chunk.id, requirement_id=mine.results[0].entity_id
    )
    # Inconsistent data: a requirement of another workspace linked to this document's chunk
    ws2 = other_workspace(db_session)
    foreign = Requirement(workspace_id=ws2.id, title="Foreign", description="d", requirement_type="GENERAL", status="DRAFT")
    db_session.add(foreign)
    db_session.flush()
    my_chunk = DocumentChunkRepository(db_session).list_by_document(doc.id)[0]
    AnalysisRepository(db_session).add_evidence_link(
        workspace_id=ws2.id, chunk_id=my_chunk.id, requirement_id=foreign.id
    )
    items = client.get(f"{base(test_workspace, doc)}/requirements").json()["items"]
    assert [i["title"] for i in items] == ["Mine"]
    assert len(items[0]["evidence"]) == 1 and items[0]["evidence"][0]["chunk_id"] == str(my_chunk.id)
    assert only_other.results[0].entity_id not in {uuid.UUID(i["requirement_id"]) for i in items}
    assert client.get(f"/api/v1/workspaces/{ws2.id}/documents/{doc.id}/requirements").status_code == 404


def test_legacy_requirement_without_provenance(client, db_session, test_workspace, doc):
    legacy = Requirement(
        workspace_id=test_workspace.id, title="Legacy", description="d", requirement_type="GENERAL",
        status="ACTIVE", normalized_value=None,
    )
    db_session.add(legacy)
    db_session.flush()
    chunk = DocumentChunkRepository(db_session).list_by_document(doc.id)[0]
    AnalysisRepository(db_session).add_evidence_link(
        workspace_id=test_workspace.id, chunk_id=chunk.id, requirement_id=legacy.id
    )
    q = client.get(f"{base(test_workspace, doc)}/requirements").json()["items"][0]
    assert q["source_quote"] is None and q["review_status"] is None and q["provenance"] is None
    assert q["normalized_value"] is None  # missing review metadata is never read as approval


def test_requirement_provenance_sanitised_and_malayalam(client, db_session, test_workspace):
    d = make_doc(db_session, test_workspace, [(f"പ്രമാണം {ML}.", [f"പ്രമാണം {ML}."])])
    r = persist(
        db_session, test_workspace, d,
        resp(reqs=[req_payload(quote=ML, title="സമർപ്പിക്കണം", normalized_value={"required_action": ML})]),
    )
    http = client.get(f"{base(test_workspace, d)}/requirements")
    q = http.json()["items"][0]
    assert q["title"] == "സമർപ്പിക്കണം" and q["source_quote"] == ML
    assert q["normalized_value"]["required_action"] == ML
    assert "_pramaan" not in q["normalized_value"]
    assert "proposal_fingerprint" not in http.text and r.results[0].fingerprint not in http.text
    assert ML in http.content.decode("utf-8")


# ══ D. evidence-protection guard (HTTP 409) ══════════════════════════════════
@pytest.fixture
def evidence_doc(client, db_session, test_workspace, temp_storage_dir, monkeypatch):
    """A really-uploaded digital PDF whose canonical chunks carry an evidence link."""
    settings = get_settings().model_copy(update={"document_storage_dir": temp_storage_dir})
    monkeypatch.setattr(
        "app.services.document_service._get_storage",
        lambda s: __import__("app.ingestion.storage", fromlist=["x"]).LocalStorageAdapter(temp_storage_dir),
    )
    text = "The department must submit the annual report on time."
    doc = upload_document(
        session=db_session, settings=settings, workspace_id=test_workspace.id,
        filename="guard.pdf", content_type="application/pdf", file_data=create_synthetic_pdf([text]),
    )
    page_text = DocumentPageRepository(db_session).get_by_page_number(doc.id, 1).extracted_text.strip()
    result = persist(
        db_session, test_workspace, doc,
        resp(facts=[fact_payload(quote=page_text, raw="annual report")]),
    )
    assert result.facts_created == 1
    # the guard must never reach deletion
    def no_delete(self, document_id):
        raise AssertionError("chunks must not be deleted while evidence exists")

    monkeypatch.setattr(DocumentChunkRepository, "delete_by_document", no_delete)
    return doc


def _state(db, doc):
    return (
        sorted(str(c.id) for c in DocumentChunkRepository(db).list_by_document(doc.id)),
        count(db, EvidenceLink),
    )


def test_ocr_trigger_409_when_evidence_exists(client, db_session, test_workspace, evidence_doc, monkeypatch):
    from app.ingestion.ocr import PageOCRResult

    monkeypatch.setattr(
        "app.services.document_service.perform_document_ocr",
        lambda pdf, targets, **kw: [
            PageOCRResult(page_number=1, recognized_text="Replacement OCR text that is long enough.", char_count=40,
                          extraction_method="OCR", extraction_status="COMPLETED", languages_used="eng")
        ],
    )
    r = client.post(f"{base(test_workspace, evidence_doc)}/ocr", json={"page_numbers": [1], "provider": "tesseract"})
    assert r.status_code == 409
    assert "evidence links" in r.json()["detail"]


def test_page_canonical_selection_409_when_evidence_exists(client, db_session, test_workspace, evidence_doc):
    before = _state(db_session, evidence_doc)
    r = client.put(f"{base(test_workspace, evidence_doc)}/pages/1/canonical", json={"provider": "native_digital"})
    assert r.status_code == 409 and "evidence links" in r.json()["detail"]
    assert _state(db_session, evidence_doc) == before


def test_document_canonical_selection_409_when_evidence_exists(client, db_session, test_workspace, evidence_doc):
    before = _state(db_session, evidence_doc)
    r = client.put(f"{base(test_workspace, evidence_doc)}/canonical", json={"provider": "native_digital"})
    assert r.status_code == 409 and "evidence links" in r.json()["detail"]
    assert _state(db_session, evidence_doc) == before
    assert before[1] >= 1 and count(db_session, DocumentChunk, document_id=evidence_doc.id) >= 1


# ══ E. routing / OpenAPI ═════════════════════════════════════════════════════
def test_routes_registered_with_correct_prefix_and_methods(client):
    paths = client.get("/openapi.json").json()["paths"]
    prefix = "/api/v1/workspaces/{workspace_id}/documents/{document_id}"
    assert list(paths[f"{prefix}/extract"]) == ["post"]
    assert list(paths[f"{prefix}/facts"]) == ["get"]
    assert list(paths[f"{prefix}/requirements"]) == ["get"]
    assert not any("/documents/{document_id}/documents" in p or p.count("/api/v1") > 1 for p in paths)
    extract = paths[f"{prefix}/extract"]["post"]
    assert "requestBody" not in extract
    for code in ("200", "404", "409", "413", "429", "500", "502", "503", "504"):
        assert code in extract["responses"], code
    assert "ExtractionRunResponse" in extract["responses"]["429"]["content"]["application/json"]["schema"]["$ref"]
    assert "max_model_calls_per_request" in extract["description"]


def test_existing_document_routes_still_work(client, db_session, test_workspace, doc):
    assert client.get(base(test_workspace, doc)).status_code == 200
    assert client.get(f"{base(test_workspace, doc)}/chunks").json()["total"] == 2


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (ExtractionTruncatedError("cut"), "RESPONSE_TRUNCATED"),
        (ExtractionRefusalError("no"), "MODEL_REFUSAL"),
        (ExtractionUnexpectedToolCallsError("tools"), "UNEXPECTED_TOOL_CALLS"),
        (ExtractionUnexpectedFinishReasonError("odd"), "UNEXPECTED_FINISH_REASON"),
    ],
)
def test_new_response_failure_reasons_map_to_502_with_full_body(client, db_session, test_workspace, doc, exc, reason):
    use_extractor(FakeExtractor({CHUNK_A: exc}))
    r = client.post(f"{base(test_workspace, doc)}/extract")
    assert r.status_code == 502
    body = r.json()
    assert body["status"] == "FAILED" and body["extraction_run_id"] and "detail" not in body
    assert body["pages"][0]["reason"] == reason
    assert QUOTE not in r.text
