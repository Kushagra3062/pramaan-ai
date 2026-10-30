import logging
import time

from app.core.config import get_settings
from app.core.meta_client import get_meta_client

logger = logging.getLogger(__name__)


def smoke_test_model() -> dict[str, object]:
    """Make one minimal provider call; never log prompt content or credentials."""
    settings = get_settings()
    client = get_meta_client()
    started = time.perf_counter()
    response = client.responses.create(
        model=settings.meta_model_id,
        input="Reply with the single word READY.",
        max_output_tokens=20,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    text = (response.output_text or "").strip()
    usage = getattr(response, "usage", None)
    usage_dict = usage.model_dump() if usage and hasattr(usage, "model_dump") else {}

    logger.info(
        "meta_smoke_test_completed",
        extra={
            "event": "meta_smoke_test.completed",
            "model": settings.meta_model_id,
            "duration_ms": elapsed_ms,
            "has_output": bool(text),
        },
    )
    return {
        "status": "ok" if text else "empty_output",
        "model": settings.meta_model_id,
        "output": text,
        "duration_ms": elapsed_ms,
        "usage": usage_dict,
    }
