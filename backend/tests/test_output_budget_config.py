"""META_MAX_OUTPUT_TOKENS default and request wiring (Milestone 4B.5C.3). Offline; ignores the local .env."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.ingestion.structured_extractor import (
    ExtractionTruncatedError,
    StructuredExtractor,
)

VALID = json.dumps({"facts": [], "requirements": []})


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("META_MAX_OUTPUT_TOKENS", raising=False)


def settings(**kw) -> Settings:
    return Settings(_env_file=None, model_api_key="sk-test", **kw)


def _response(content=VALID, finish="stop"):
    message = SimpleNamespace(content=content, refusal=None, tool_calls=None, function_call=None)
    usage = SimpleNamespace(prompt_tokens=955, completion_tokens=3000, completion_tokens_details=SimpleNamespace(reasoning_tokens=2998))
    return SimpleNamespace(id="chatcmpl-1", model="muse-spark-1.3", choices=[SimpleNamespace(message=message, finish_reason=finish)], usage=usage)


def _call(monkeypatch, cfg, response=None):
    client = MagicMock()
    client.chat.completions.create.return_value = response or _response()
    monkeypatch.setattr("app.ingestion.structured_extractor.get_meta_client", lambda: client)
    StructuredExtractor(cfg).extract_from_text("Some text.")
    return client.chat.completions.create.call_args.kwargs


# 1-4: settings
def test_default_budget_is_8000_without_overrides():
    assert settings().meta_max_output_tokens == 8000


def test_environment_override_is_respected(monkeypatch):
    monkeypatch.setenv("META_MAX_OUTPUT_TOKENS", "5000")
    assert settings().meta_max_output_tokens == 5000


def test_maximum_allowed_value_remains_8000():
    assert settings(meta_max_output_tokens=8000).meta_max_output_tokens == 8000
    le = next(m.le for m in Settings.model_fields["meta_max_output_tokens"].metadata if hasattr(m, "le"))
    assert le == 8000


@pytest.mark.parametrize("bad", [8001, 20000, 19])
def test_out_of_range_values_are_rejected(bad):
    with pytest.raises(ValidationError):
        settings(meta_max_output_tokens=bad)


def test_env_value_above_maximum_is_rejected(monkeypatch):
    monkeypatch.setenv("META_MAX_OUTPUT_TOKENS", "9000")
    with pytest.raises(ValidationError):
        settings()


# 5-7: request wiring
def test_extractor_sends_the_configured_budget(monkeypatch):
    assert _call(monkeypatch, settings())["max_tokens"] == 8000  # default
    assert _call(monkeypatch, settings(meta_max_output_tokens=6000))["max_tokens"] == 6000
    assert _call(monkeypatch, settings(meta_max_output_tokens=8000))["max_tokens"] == 8000


def test_request_is_not_pinned_to_3000(monkeypatch):
    assert _call(monkeypatch, settings(meta_max_output_tokens=7000))["max_tokens"] != 3000


def test_other_request_parameters_unchanged(monkeypatch):
    kwargs = _call(monkeypatch, settings())
    assert set(kwargs) == {"model", "messages", "max_tokens", "temperature"}  # no response_format / reasoning_effort
    assert kwargs["temperature"] == 0.0 and kwargs["model"] == "muse-spark-1.3"
    assert [m["role"] for m in kwargs["messages"]] == ["system", "user"]


# 8-9: classification and diagnostics preserved
def test_truncation_still_rejected_at_the_new_budget(monkeypatch):
    with pytest.raises(ExtractionTruncatedError) as info:
        _call(monkeypatch, settings(), _response(content=VALID, finish="length"))
    d = info.value.diagnostics
    assert d.finish_reason == "length" and d.request_id == "chatcmpl-1"
    assert (d.input_tokens, d.output_tokens, d.reasoning_tokens) == (955, 3000, 2998)
