"""Offline tests for Langfuse scores API and run traces (fake client only)."""

from __future__ import annotations

from app.core import tracing
from app.core.tracing import Tracer


class _FakeScoresClient:
    def __init__(self) -> None:
        self.scores: list[dict] = []

    def create_score(self, **kwargs):
        self.scores.append(kwargs)

    def flush(self) -> None:
        return None

    def shutdown(self) -> None:
        return None


def test_record_score_submits_and_flush_safe():
    client = _FakeScoresClient()
    tracer = Tracer(client)
    assert tracer.record_score(name="extraction.fact_f1", value=0.92, trace_id="trace-1") is True
    assert client.scores[0]["name"] == "extraction.fact_f1"
    assert client.scores[0]["value"] == 0.92


def test_record_score_rejects_bad_values_without_call():
    client = _FakeScoresClient()
    tracer = Tracer(client)
    assert tracer.record_score(name="", value=1.0) is False
    assert tracer.record_score(name="x", value=float("nan")) is True  # numeric; Langfuse validates range
    assert tracer.record_score(name="x", value=True) is False
    assert len(client.scores) == 1


def test_record_score_inactive_tracer_returns_false():
    assert Tracer(None).record_score(name="x", value=1.0) is False


def test_record_score_failure_disables_tracer():
    class _Boom(_FakeScoresClient):
        def create_score(self, **kwargs):
            raise RuntimeError("network down")

    tracer = Tracer(_Boom())
    assert tracer.record_score(name="x", value=1.0) is False
    assert tracer.active is False


def test_evaluation_scores_push_uses_injected_tracer(monkeypatch):
    client = _FakeScoresClient()
    monkeypatch.setattr(tracing, "_tracer", Tracer(client))
    assert tracing.record_evaluation_scores(fact_f1=1.0, requirement_f1=0.5, quote_match_rate=None) is True
    assert [score["name"] for score in client.scores] == ["extraction.fact_f1", "extraction.requirement_f1"]
    tracing.reset_tracer()
