"""Tests for the opt-in Meta diagnostic script. No network, no real extractor, nothing persisted."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.repositories.document_repo import DocumentChunkRepository, DocumentPageRepository
from app.ingestion.structured_extractor import ExtractionTruncatedError, ResponseDiagnostics
from app.models.audit_eval import AuditEvent, ModelUsage
from app.models.document import DocumentChunk
from app.models.fact_requirement import ExtractedFact, Requirement
from app.models.workspace import Workspace
from tests.test_extraction_orchestrator import FakeExtractor, ok
from tests.test_extraction_persistence import PAGE1, QUOTE, fact_payload, make_doc

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "diagnose_meta_extraction.py"
_spec = importlib.util.spec_from_file_location("diagnose_meta_extraction", _SCRIPT)
script = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = script  # dataclasses resolve their module through sys.modules
_spec.loader.exec_module(script)

CHUNK_TEXT = f"SECRET-CHUNK-TEXT-999 {QUOTE}"


def _boom(*a, **k):
    raise AssertionError("must not be reached without both safety gates")


def _argv(doc_id=None, page=1, chunk_id=None, execute=True):
    args = ["--document-id", str(doc_id or uuid.uuid4()), "--page-number", str(page), "--chunk-id", str(chunk_id or uuid.uuid4())]
    return (["--execute"] if execute else []) + args


@pytest.fixture
def doc(db_session, test_workspace):
    return make_doc(db_session, test_workspace, [(PAGE1, [CHUNK_TEXT])])


def _chunk_of(db, doc):
    return DocumentChunkRepository(db).list_by_document(doc.id)[0]


def _counts(db):
    return tuple(db.scalar(select(func.count()).select_from(m)) for m in (ExtractedFact, Requirement, ModelUsage, AuditEvent))


# ── safety gates (23) ────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("execute", "env"),
    [
        (False, {"RUN_META_DIAGNOSTIC": "true"}),
        (True, {}),
        (True, {"RUN_META_DIAGNOSTIC": "false"}),
        (True, {"RUN_META_DIAGNOSTIC": "1"}),
        (False, {}),
    ],
)
def test_refuses_without_both_gates(capsys, execute, env):
    code = script.main(_argv(execute=execute), env=env, session_factory=_boom, extractor_factory=_boom)
    assert code == script.EXIT_REFUSED
    assert "Refusing to run" in capsys.readouterr().out


def test_gates_helper():
    assert script.gates_open(True, {"RUN_META_DIAGNOSTIC": "TRUE"})
    assert not script.gates_open(True, {}) and not script.gates_open(False, {"RUN_META_DIAGNOSTIC": "true"})


def test_text_cannot_be_passed_on_command_line(capsys):
    with pytest.raises(SystemExit):
        script.main(["--execute", "--text", "arbitrary", "--document-id", str(uuid.uuid4()), "--page-number", "1",
                     "--chunk-id", str(uuid.uuid4())], env={"RUN_META_DIAGNOSTIC": "true"})


# ── provenance validation (24) ───────────────────────────────────────────────
def test_valid_canonical_chunk_loads(db_session, doc):
    chunk = _chunk_of(db_session, doc)
    trusted = script.load_trusted_chunk(db_session, doc.id, 1, chunk.id)
    assert trusted.text == CHUNK_TEXT and trusted.char_count == len(CHUNK_TEXT) and trusted.chunk_id == chunk.id


def test_rejects_missing_or_deleted_document(db_session, doc):
    chunk = _chunk_of(db_session, doc)
    with pytest.raises(script.DiagnosticInputError, match="document"):
        script.load_trusted_chunk(db_session, uuid.uuid4(), 1, chunk.id)
    doc.deleted_at = datetime.now(UTC)
    db_session.flush()
    with pytest.raises(script.DiagnosticInputError, match="document"):
        script.load_trusted_chunk(db_session, doc.id, 1, chunk.id)


def test_rejects_missing_page_and_non_completed_page(db_session, test_workspace, doc):
    chunk = _chunk_of(db_session, doc)
    with pytest.raises(script.DiagnosticInputError, match="page not found"):
        script.load_trusted_chunk(db_session, doc.id, 7, chunk.id)
    page = DocumentPageRepository(db_session).get_by_page_number(doc.id, 1)
    page.extraction_status = "NEEDS_OCR"
    db_session.flush()
    with pytest.raises(script.DiagnosticInputError, match="COMPLETED"):
        script.load_trusted_chunk(db_session, doc.id, 1, chunk.id)


def test_rejects_page_with_no_canonical_text(db_session, test_workspace):
    d = make_doc(db_session, test_workspace, [("[OCR]:   ", ["x chunk"])])
    with pytest.raises(script.DiagnosticInputError, match="no canonical text"):
        script.load_trusted_chunk(db_session, d.id, 1, _chunk_of(db_session, d).id)


def test_rejects_chunk_of_other_document_or_workspace_or_page(db_session, test_workspace, doc):
    other_doc = make_doc(db_session, test_workspace, [(PAGE1, ["other doc chunk"])])
    with pytest.raises(script.DiagnosticInputError, match="does not belong to this document"):
        script.load_trusted_chunk(db_session, doc.id, 1, _chunk_of(db_session, other_doc).id)

    ws2 = Workspace(name="O", slug=f"o-{uuid.uuid4().hex[:8]}")
    db_session.add(ws2)
    db_session.flush()
    foreign = DocumentChunkRepository(db_session).create(
        workspace_id=ws2.id, document_id=doc.id, page_start=1, page_end=1, chunk_index=50, content="foreign ws",
        embedding_model="m", embedding_version="1", content_hash="a" * 64)
    with pytest.raises(script.DiagnosticInputError, match="workspace"):
        script.load_trusted_chunk(db_session, doc.id, 1, foreign.id)

    cross = DocumentChunkRepository(db_session).create(
        workspace_id=test_workspace.id, document_id=doc.id, page_start=1, page_end=2, chunk_index=51,
        content="cross page", embedding_model="m", embedding_version="1", content_hash="b" * 64)
    with pytest.raises(script.DiagnosticInputError, match="single-page"):
        script.load_trusted_chunk(db_session, doc.id, 1, cross.id)
    two_page = make_doc(db_session, test_workspace, [(PAGE1, ["page one chunk"]), ("Page two text here.", ["page two chunk"])])
    page1_chunk = DocumentChunkRepository(db_session).list_by_document(two_page.id)[0]
    with pytest.raises(script.DiagnosticInputError, match="single-page"):  # chunk of page 1 requested as page 2
        script.load_trusted_chunk(db_session, two_page.id, 2, page1_chunk.id)

    blank = DocumentChunkRepository(db_session).create(
        workspace_id=test_workspace.id, document_id=doc.id, page_start=1, page_end=1, chunk_index=52,
        content="   ", embedding_model="m", embedding_version="1", content_hash="c" * 64)
    with pytest.raises(script.DiagnosticInputError, match="empty"):
        script.load_trusted_chunk(db_session, doc.id, 1, blank.id)
    with pytest.raises(script.DiagnosticInputError, match="chunk not found"):
        script.load_trusted_chunk(db_session, doc.id, 1, uuid.uuid4())


def test_invalid_input_makes_no_model_request(db_session, doc, capsys):
    code = script.main(_argv(doc.id, 1, uuid.uuid4()), env={"RUN_META_DIAGNOSTIC": "true"},
                       session_factory=lambda: db_session, extractor_factory=_boom)
    assert code == script.EXIT_INVALID_INPUT
    assert "No model request was made" in capsys.readouterr().out


# ── exactly one request, metadata-only output, nothing persisted (25) ────────
def test_single_request_prints_only_metadata_and_persists_nothing(db_session, doc, capsys):
    chunk = _chunk_of(db_session, doc)
    before = _counts(db_session)
    diag = ResponseDiagnostics(request_id="chatcmpl-9", finish_reason="stop", input_tokens=800, output_tokens=1200,
                               reasoning_tokens=700, content_length=321, choices_count=1)
    result = ok([fact_payload()])
    result = type(result)(response=result.response, model_id=result.model_id, prompt_version=result.prompt_version,
                          latency_ms=1.0, request_id="chatcmpl-9", input_tokens=800, output_tokens=1200, diagnostics=diag)
    fx = FakeExtractor({CHUNK_TEXT: result})
    code = script.main(_argv(doc.id, 1, chunk.id), env={"RUN_META_DIAGNOSTIC": "true"},
                       session_factory=lambda: db_session, extractor_factory=lambda: fx)
    out = capsys.readouterr().out
    assert code == 0 and fx.calls == [CHUNK_TEXT]  # exactly one request, with the trusted chunk text
    for expected in ("document_id", "page_number", "chunk_id", "chunk_chars", "model_id", "prompt_version", "request_id: chatcmpl-9",
                     "finish_reason: stop", "input_tokens: 800", "output_tokens: 1200", "reasoning_tokens: 700",
                     "content_length: 321", "refusal_present", "tool_calls_count", "json_parsed: True"):
        assert expected in out, expected
    assert "SECRET-CHUNK-TEXT-999" not in out and QUOTE not in out and "30 November" not in out  # no text, no values
    assert _counts(db_session) == before  # no facts, requirements, ModelUsage or AuditEvent
    assert count_chunks(db_session, doc) == 1


def test_failure_prints_category_and_diagnostics_without_text(db_session, doc, capsys):
    chunk = _chunk_of(db_session, doc)
    diag = ResponseDiagnostics(request_id="chatcmpl-7", finish_reason="length", input_tokens=900, output_tokens=3000,
                               reasoning_tokens=2950, content_length=0, choices_count=1)
    fx = FakeExtractor({CHUNK_TEXT: ExtractionTruncatedError("x", diag)})
    code = script.main(_argv(doc.id, 1, chunk.id), env={"RUN_META_DIAGNOSTIC": "true"},
                       session_factory=lambda: db_session, extractor_factory=lambda: fx)
    out = capsys.readouterr().out
    assert code == script.EXIT_REQUEST_FAILED and len(fx.calls) == 1
    assert "error_category: RESPONSE_TRUNCATED" in out and "finish_reason: length" in out and "json_parsed: not_attempted" in out
    assert "SECRET-CHUNK-TEXT-999" not in out


def test_unknown_values_print_as_unknown_not_zero(db_session, doc, capsys):
    chunk = _chunk_of(db_session, doc)

    class Boom(FakeExtractor):
        def extract_from_text(self, source_text):
            raise RuntimeError("MODEL_API_KEY is not configured: sk-secret")

    script.main(_argv(doc.id, 1, chunk.id), env={"RUN_META_DIAGNOSTIC": "true"},
                session_factory=lambda: db_session, extractor_factory=lambda: Boom())
    out = capsys.readouterr().out
    assert "error_category: UNEXPECTED_ERROR" in out and "input_tokens: unknown" in out and "request_id: unknown" in out
    assert "sk-secret" not in out and "MODEL_API_KEY" not in out


def count_chunks(db, doc):
    return db.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.document_id == doc.id))
