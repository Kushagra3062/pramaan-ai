from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Pramaan AI"
    app_env: str = "local"
    log_level: str = "INFO"
    allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    database_url: str = "postgresql+psycopg://pramaan:pramaan_local_only@127.0.0.1:5433/pramaan_dev"

    model_api_key: str | None = None
    meta_base_url: str = "https://api.meta.ai/v1"
    meta_model_id: str = "muse-spark-1.3"
    meta_timeout_seconds: float = Field(default=45, ge=5, le=120)
    meta_max_output_tokens: int = Field(default=500, ge=20, le=4000)

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
    document_storage_dir: str = "../uploads"
    min_text_density_chars: int = Field(default=30, ge=5, le=500)

    # OCR configuration
    ocr_languages: str = "eng+mal"
    ocr_dpi: int = Field(default=300, ge=72, le=600)
    ocr_max_pages_per_request: int = Field(default=20, ge=1, le=100)
    tessdata_prefix: str | None = None

    # Chunking configuration
    chunk_size: int = Field(default=1000, ge=100, le=10000)
    chunk_overlap: int = Field(default=200, ge=0, le=2000)

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
