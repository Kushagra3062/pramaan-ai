import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.db.engine import check_database

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health/live")
def liveness() -> dict[str, str]:
    return {"status": "ok", "check": "live"}


@router.get("/health/ready")
def readiness() -> dict[str, str]:
    try:
        check_database()
    except (SQLAlchemyError, OSError):
        logger.exception("readiness_database_check_failed", extra={"event": "health.db_failed"})
        raise HTTPException(status_code=503, detail="Database is not ready") from None
    return {"status": "ok", "check": "ready", "database": "ok"}
