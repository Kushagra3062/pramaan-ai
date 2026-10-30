import logging

from fastapi import APIRouter, Depends, HTTPException
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.engine import get_db
from app.db.repositories.workspace_repo import WorkspaceRepository
from app.services.meta_service import smoke_test_model
from fastapi import Depends
from sqlalchemy.orm import Session

router = APIRouter(prefix="/internal/dev", tags=["development"])
logger = logging.getLogger(__name__)


@router.get("/workspace-default")
def get_default_test_workspace(session: Session = Depends(get_db)) -> dict[str, str]:
    """Get or create a default testing workspace for development and frontend test suite."""
    repo = WorkspaceRepository(session)
    ws = repo.get_by_slug("default-test-workspace")
    if not ws:
        ws = repo.create(name="Default Test Workspace", slug="default-test-workspace")
        session.commit()
    return {"workspace_id": str(ws.id), "name": ws.name, "slug": ws.slug}


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
