from functools import lru_cache

from openai import OpenAI

from app.core.config import get_settings


@lru_cache
def get_meta_client() -> OpenAI:
    settings = get_settings()
    if not settings.model_api_key:
        raise RuntimeError("MODEL_API_KEY is not configured")
    return OpenAI(
        base_url=settings.meta_base_url,
        api_key=settings.model_api_key,
        timeout=settings.meta_timeout_seconds,
        max_retries=0,  # Retries are handled explicitly by the caller/service policy.
    )
