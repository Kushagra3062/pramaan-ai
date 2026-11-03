"""Offline checks for production safety validation."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


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
