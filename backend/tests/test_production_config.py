"""Offline checks for production safety validation."""

import re

import pytest
from pydantic import ValidationError

from app.core.config import Settings

PREVIEW_REGEX = r"https://pramaan-[a-z0-9]+-kushagra3062s-projects\.vercel\.app$"


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "production",
        "model_api_key": "test-only-key",
        "supabase_url": "https://project.supabase.co",
        "storage_backend": "gcs",
        "gcs_bucket_name": "private-documents",
        "database_url": "postgresql+psycopg://user:password@example.com:5432/pramaan?sslmode=require",
        "allowed_origins": "https://demo.example.com",
    }
    values.update(overrides)
    return Settings(**values)


def test_production_settings_require_gcs_tls_and_https_origins():
    settings = production_settings()

    assert settings.storage_backend == "gcs"
    assert settings.cors_origins == ["https://demo.example.com"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"storage_backend": "local"},
        {"gcs_bucket_name": None},
        {"database_url": "postgresql+psycopg://user:password@example.com:5432/pramaan"},
        {"allowed_origins": "http://localhost:3000"},
        {"model_api_key": None},
        {"supabase_url": None},
        {"supabase_url": "http://project.supabase.co"},
    ],
)
def test_unsafe_production_settings_are_rejected(overrides: dict[str, object]):
    with pytest.raises(ValidationError):
        production_settings(**overrides)


def test_preview_origin_regex_is_accepted_and_matches_team_previews():
    settings = production_settings(allowed_origin_regex=PREVIEW_REGEX)
    assert settings.allowed_origin_regex == PREVIEW_REGEX
    # Starlette matches with re.match; the $ anchor blocks suffix attacks.
    assert re.match(PREVIEW_REGEX, "https://pramaan-6j8jhz71c-kushagra3062s-projects.vercel.app")
    assert re.match(PREVIEW_REGEX, "https://pramaan-patizsjj0-kushagra3062s-projects.vercel.app")
    assert not re.match(PREVIEW_REGEX, "https://pramaan-evil-example.vercel.app")
    assert not re.match(PREVIEW_REGEX, "https://pramaan-abc-kushagra3062s-projects.vercel.app.evil.com")
    assert not re.match(PREVIEW_REGEX, "http://pramaan-abc-kushagra3062s-projects.vercel.app")


@pytest.mark.parametrize(
    "overrides",
    [
        {"allowed_origin_regex": "http://pramaan-.*\\.vercel\\.app"},
        {"allowed_origin_regex": "https://pramaan-(unclosed"},
        {"allowed_origin_regex": ".*"},
    ],
)
def test_unsafe_origin_regex_is_rejected(overrides: dict[str, object]):
    with pytest.raises(ValidationError):
        production_settings(**overrides)
