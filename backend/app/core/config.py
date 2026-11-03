from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Pramaan AI"
    app_env: str = "local"
    log_level: str = "INFO"
    allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    database_url: str = "postgresql+psycopg://pramaan:pramaan_local_only@127.0.0.1:5433/pramaan_dev"

    model_api_key: str | None = None
    supabase_url: str | None = None
    supabase_jwt_audience: str = "authenticated"
    meta_base_url: str = "https://api.meta.ai/v1"
    meta_model_id: str = "muse-spark-1.3"
    meta_timeout_seconds: float = Field(default=60, ge=5, le=180)
    meta_max_output_tokens: int = Field(default=8000, ge=20, le=8000)

    # Langfuse LLM observability. Tracing stays inactive (and extraction unaffected)
    # unless both keys are configured and it is enabled here.
    langfuse_enabled: bool = True
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_base_url: str = "https://cloud.langfuse.com"

    # Safety / demo bounds
    max_upload_mb: int = Field(default=10, ge=1, le=50)
    max_pdf_pages: int = Field(default=20, ge=1, le=100)
    max_agent_steps: int = Field(default=3, ge=1, le=5)
    max_model_calls_per_request: int = Field(default=4, ge=1, le=10)
    daily_model_call_limit: int = Field(default=20, ge=1, le=1000)

    # Document storage
    storage_backend: Literal["local", "gcs", "supabase"] = "local"
    document_storage_dir: str = "../uploads"
    gcs_bucket_name: str | None = None
    gcs_project_id: str | None = None
    supabase_storage_bucket: str | None = None
    supabase_service_role_key: str | None = None
    min_text_density_chars: int = Field(default=30, ge=5, le=500)

    # OCR configuration
    ocr_languages: str = "eng+mal"
    ocr_dpi: int = Field(default=300, ge=72, le=600)
    ocr_max_pages_per_request: int = Field(default=20, ge=1, le=100)
    tessdata_prefix: str | None = None

    # Vision & Provider Selection
    scanned_page_extractor: str = Field(default="tesseract", description="tesseract | meta_vision | compare")
    meta_vision_fallback_to_tesseract: bool = True
    meta_vision_dpi: int = Field(default=250, ge=72, le=600)
    meta_vision_max_image_dim: int = Field(default=2048, ge=512, le=4096)

    # Chunking configuration (Character-based limits, not tokens)
    chunk_size: int = Field(
        default=1000,
        ge=100,
        le=10000,
        description="Target maximum chunk length in characters (not tokens). Boundary logic preserves complete words and sentences.",
    )
    chunk_overlap: int = Field(
        default=200,
        ge=0,
        le=2000,
        description="Target chunk overlap in characters (not tokens) on the same page, snapped to complete word boundaries.",
    )

    # Embedding configuration
    embedding_model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dimension: int = Field(default=384, ge=64, le=4096)
    embedding_version: str = "1.0.0"

    model_config = SettingsConfigDict(
        env_file="../.env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origins(self) -> list[str]:
        return [value.strip() for value in self.allowed_origins.split(",") if value.strip()]

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        """Reject unsafe production defaults before the application starts."""
        if self.app_env.lower() not in {"production", "prod"}:
            return self

        if not self.model_api_key:
            raise ValueError("MODEL_API_KEY is required when APP_ENV is production")
        if not self.supabase_url or not self.supabase_url.startswith("https://"):
            raise ValueError("production requires an HTTPS SUPABASE_URL for JWT verification")
        if self.storage_backend == "gcs" and not self.gcs_bucket_name:
            raise ValueError("GCS_BUCKET_NAME is required when STORAGE_BACKEND=gcs")
        if self.storage_backend == "supabase" and (not self.supabase_storage_bucket or not self.supabase_service_role_key):
            raise ValueError("Supabase private storage requires SUPABASE_STORAGE_BUCKET and SUPABASE_SERVICE_ROLE_KEY")
        if self.storage_backend not in {"gcs", "supabase"}:
            raise ValueError("production requires durable gcs or supabase document storage")
        if not self.database_url.startswith("postgresql+") or "sslmode=require" not in self.database_url:
            raise ValueError("production DATABASE_URL must use PostgreSQL with sslmode=require")
        if not self.cors_origins or any(not origin.startswith("https://") for origin in self.cors_origins):
            raise ValueError("production ALLOWED_ORIGINS must contain only explicit HTTPS origins")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
