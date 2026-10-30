import logging

from fastapi import APIRouter, HTTPException
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

from app.core.config import get_settings
from app.services.meta_service import smoke_test_model

router = APIRouter(prefix="/internal/dev", tags=["development"])
logger = logging.getLogger(__name__)


@router.post("/meta-smoke")
def meta_smoke() -> dict[str, object]:
    """Development-only endpoint. Do not expose this router in a public production deployment."""
    if get_settings().app_env.lower() not in {"local", "development", "test"}:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        return smoke_test_model()
    except (APIConnectionError, APITimeoutError):
        logger.exception("meta_api_connection_failed", extra={"event": "meta_api.connection_failed"})
        raise HTTPException(status_code=502, detail="Meta API connection failed or timed out") from None
    except RateLimitError:
        logger.exception("meta_api_rate_limited", extra={"event": "meta_api.rate_limited"})
        raise HTTPException(status_code=429, detail="Meta API rate limit reached") from None
    except APIStatusError as exc:
        logger.exception(
            "meta_api_status_error",
            extra={"event": "meta_api.status_error", "provider_status": exc.status_code},
        )
        raise HTTPException(status_code=502, detail=f"Meta API returned status {exc.status_code}") from None
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
