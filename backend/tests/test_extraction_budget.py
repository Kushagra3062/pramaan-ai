"""Daily budget enforcement across the extraction pipeline (PostgreSQL-backed).

The Meta client and the Langfuse client are fakes; the budget counter is real PostgreSQL on a
far-future UTC date that is purged before and after every test.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text

from app.api.routes.extraction import get_structured_extractor
from app.core import metrics, tracing
from app.core.config import get_settings
from app.db.engine import SessionLocal, engine
from app.ingestion.structured_extractor import ExtractionTimeoutError, StructuredExtractor
from app.main import app
from app.models.audit_eval import AuditEvent, ModelUsage
from app.models.fact_requirement import ExtractedFact
from app.services import model_budget as budget_module
from app.services.extraction_orchestrator import RunStatus, run_document_extraction
from app.services.model_budget import (
    ModelBudgetExceededError,
    PostgresBudgetStore,
    PostgresDailyModelBudget,
)
from tests.test_extraction_orchestrator import FakeExtractor, ok
from tests.test_extraction_persistence import PAGE1, QUOTE, fact_payload, make_doc
from tests.test_tracing import FakeClient

TEST_DAY = date(2090, 1, 1)
CHUNK = f"Intro paragraph. {QUOTE}"
TAIL = "Closing remarks."


@pytest.fixture(autouse=True)
def _clean_state():
    metrics.reset()
    _purge()
    yield
    metrics.reset()
    _purge()


def _purge() -> None:
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM model_call_budget WHERE budget_date >= DATE '2090-01-01'")
        )


def make_budget(limit: int) -> PostgresDailyModelBudget:
    return PostgresDailyModelBudget(
        limit=limit,
        store=PostgresBudgetStore(SessionLocal),
        clock=lambda: datetime(2090, 1, 1, 12, 0, tzinfo=UTC),
    )


@pytest.fixture
def budget() -> PostgresDailyModelBudget:
    installed = make_budget(5)
    budget_module.override_model_budget(installed)
    yield installed
    budget_module.reset_model_budget()


def counter() -> int:
    return PostgresBudgetStore(SessionLocal).current(budget_date=TEST_DAY)


def count(db, model, **filters) -> int:
    statement = select(func.count()).select_from(model)
    for key, value in filters.items():
        statement = statement.where(getattr(model, key) == value)
    return db.scalar(statement)


class BudgetedFakeExtractor(FakeExtractor):
    """A fake that declares it would spend provider calls (so it charges the budget)."""

    calls_model_provider = True


@pytest.fixture
def doc(db_session, test_workspace):
    return make_doc(db_session, test_workspace, [(PAGE1, [CHUNK, TAIL])])


def run(db, ws, doc, extractor):
    return run_document_extraction(
        db, workspace_id=ws.id, document_id=doc.id, extractor=extractor
    )


# ── Orchestration accounting ─────────────────────────────────────────────────


def test_one_slot_per_request_and_no_held_capacity_after_success(
    db_session, test_workspace, doc, budget
):
    fx = BudgetedFakeExtractor()
    result = run(db_session, test_workspace, doc, fx)
    assert result.status is RunStatus.COMPLETED
    assert len(fx.calls) == 2
    assert counter() == 2  # two requests, nothing left reserved


def test_insufficient_capacity_rejects_the_whole_run_before_calling(
    db_session, test_workspace, doc
):
    budget_module.override_model_budget(make_budget(1))  # 2 chunks needed
    try:
        fx = BudgetedFakeExtractor()
        with pytest.raises(ModelBudgetExceededError):
            run(db_session, test_workspace, doc, fx)
        assert fx.calls == []
        assert counter() == 0
        assert count(db_session, ExtractedFact) == 0
        assert count(db_session, ModelUsage) == 0
        assert count(db_session, AuditEvent) == 0
    finally:
        budget_module.reset_model_budget()


def test_provider_error_keeps_the_attempted_slot_and_releases_the_rest(
    db_session, test_workspace, doc, budget
):
    fx = BudgetedFakeExtractor({CHUNK: ExtractionTimeoutError("slow")})
    result = run(db_session, test_workspace, doc, fx)
    assert result.status is RunStatus.FAILED
    assert len(fx.calls) == 1  # systemic failure stops the run
    assert counter() == 1  # the attempted request stays charged
    assert count(db_session, ModelUsage) == 1  # failed attempt still recorded


def test_pre_send_failure_refunds_its_slot(db_session, test_workspace, doc, budget):
    class Broken(BudgetedFakeExtractor):
        def extract_from_text(self, source_text):
            self.calls.append(source_text)
            raise RuntimeError("MODEL_API_KEY is not configured")

    fx = Broken()
    result = run(db_session, test_workspace, doc, fx)
    assert result.pages[0].reason.value == "EXTRACTOR_UNAVAILABLE"
    assert counter() == 0  # no request left the process, so nothing was spent


def test_plain_fake_extractor_never_charges_the_budget(db_session, test_workspace, doc, budget):
    fx = FakeExtractor()  # no calls_model_provider marker
    result = run(db_session, test_workspace, doc, fx)
    assert result.status is RunStatus.COMPLETED
    assert len(fx.calls) == 2
    assert counter() == 0


def test_budget_is_not_token_accounting(db_session, test_workspace, doc, budget):
    fx = BudgetedFakeExtractor()
    run(db_session, test_workspace, doc, fx)
    rows = list(db_session.scalars(select(ModelUsage)).all())
    assert len(rows) == 2
    assert all(row.input_tokens == 10 and row.output_tokens == 5 for row in rows)
    assert counter() == 2  # one slot per call, independent of token numbers


def test_systemic_stop_releases_capacity_for_remaining_pages(db_session, test_workspace, budget):
    multi = make_doc(
        db_session,
        test_workspace,
        [(PAGE1, [CHUNK]), ("Second page text here.", ["p2"])],
    )
    fx = BudgetedFakeExtractor({CHUNK: ExtractionTimeoutError("slow")})
    result = run(db_session, test_workspace, multi, fx)
    assert result.status is RunStatus.FAILED
    assert result.pages_not_processed == 1
    assert counter() == 1  # reserved 2, attempted 1, released 1


# ── HTTP behaviour ───────────────────────────────────────────────────────────


def use_extractor(fx) -> None:
    app.dependency_overrides[get_structured_extractor] = lambda: fx


def base(ws, doc) -> str:
    return f"/api/v1/workspaces/{ws.id}/documents/{doc.id}"


def test_http_429_when_daily_budget_is_exhausted(client, db_session, test_workspace, doc):
    budget_module.override_model_budget(make_budget(1))
    fx = BudgetedFakeExtractor()
    use_extractor(fx)
    response = client.post(f"{base(test_workspace, doc)}/extract")

    assert response.status_code == 429
    assert response.headers["Retry-After"].isdigit()
    assert int(response.headers["Retry-After"]) > 0
    body = response.json()
    assert "budget" in body["detail"].lower() and "detail" in body
    assert "extraction_run_id" not in body  # no run summary: nothing ran
    assert fx.calls == []
    assert QUOTE not in response.text and CHUNK not in response.text
    assert count(db_session, ExtractedFact) == 0
    assert count(db_session, ModelUsage) == 0
    assert count(db_session, AuditEvent) == 0
    assert counter() == 0
    assert metrics.total(metrics.METRIC_USAGE_LIMIT_REJECTIONS, limit="daily") == 1


def test_429_logs_a_typed_event_without_content(client, db_session, test_workspace, doc, caplog):
    caplog.set_level(logging.WARNING)
    budget_module.override_model_budget(make_budget(1))
    use_extractor(BudgetedFakeExtractor())

    client.post(f"{base(test_workspace, doc)}/extract")

    record = next(r for r in caplog.records if getattr(r, "event", "") == "usage.limit_rejected")
    assert record.limit == "daily"
    # Nothing was reserved yet, so 1 call of the limit of 1 remains while the run needs 2.
    assert (record.requested_calls, record.remaining_calls, record.daily_model_call_limit) == (2, 1, 1)
    assert record.budget_date == TEST_DAY.isoformat()
    assert QUOTE not in caplog.text and CHUNK not in caplog.text


def test_admitted_request_returns_200_and_charges_exactly_its_calls(
    client, db_session, test_workspace, doc
):
    budget_module.override_model_budget(make_budget(4))
    fx = BudgetedFakeExtractor({CHUNK: ok([fact_payload()])})
    use_extractor(fx)

    response = client.post(f"{base(test_workspace, doc)}/extract")

    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert counter() == 2


def test_per_request_cap_still_precedes_the_budget(client, db_session, test_workspace):
    wide = make_doc(db_session, test_workspace, [(PAGE1, [f"chunk {i}" for i in range(5)])])
    budget_module.override_model_budget(make_budget(1))
    fx = BudgetedFakeExtractor()
    use_extractor(fx)
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"max_model_calls_per_request": 4}
    )

    response = client.post(f"{base(test_workspace, wide)}/extract")

    assert response.status_code == 413  # existing cap wins, no reservation attempted
    assert fx.calls == []
    assert counter() == 0
    assert metrics.total(metrics.METRIC_USAGE_LIMIT_REJECTIONS, limit="per_request") == 1
    assert metrics.total(metrics.METRIC_USAGE_LIMIT_REJECTIONS, limit="daily") == 0


# ── Observability consistency ────────────────────────────────────────────────


class _FakeMeta:
    """Minimal OpenAI-compatible client returning one valid extraction payload."""

    def __init__(self) -> None:
        self.calls = 0
        self._content = json.dumps({"facts": [fact_payload()], "requirements": []})
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(
            id="chatcmpl-1",
            model="muse-spark-1.3",
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=self._content, refusal=None, tool_calls=None, function_call=None
                    ),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=5,
                completion_tokens_details=SimpleNamespace(reasoning_tokens=1),
            ),
        )


def test_langfuse_generations_match_charged_calls(db_session, test_workspace, doc, budget, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(tracing, "_tracer", tracing.Tracer(client, environment="test"))
    meta = _FakeMeta()
    monkeypatch.setattr("app.ingestion.structured_extractor.get_meta_client", lambda: meta)

    result = run(
        db_session, test_workspace, doc, StructuredExtractor(get_settings())
    )

    assert result.status is RunStatus.COMPLETED
    generations = client.roots[0].children[0].children
    assert len(generations) == meta.calls == 2  # no generation without a real request
    assert counter() == 2  # and exactly one slot per generation
