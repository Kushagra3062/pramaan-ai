import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    actions,
    analyses,
    conflicts,
    dev,
    documents,
    evaluation,
    extraction,
    graph,
    health,
    workspaces,
)
from app.core import metrics
from app.core.config import get_settings
from app.core.logging_config import (
    REQUEST_ID_HEADER,
    bind_request_id,
    configure_logging,
    reset_request_id,
)
from app.core.tracing import shutdown_tracing

configure_logging()
settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Flush buffered Langfuse observations on shutdown. Never raises."""
    yield
    shutdown_tracing()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Evidence-first document intelligence platform",
    docs_url="/docs" if settings.app_env.lower() != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)

app.include_router(health.router)
app.include_router(workspaces.router)
app.include_router(documents.router)
app.include_router(extraction.router)
app.include_router(conflicts.router)
app.include_router(actions.router)
app.include_router(graph.router)
app.include_router(analyses.router)
app.include_router(evaluation.router)
if settings.app_env.lower() in {"local", "development", "test"}:
    app.include_router(dev.router)


def _route_label(request: Request) -> str:
    """Route template, never the raw path, so metric dimensions stay bounded."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


def _record_http_metrics(
    request: Request, route: str, status_code: int, duration_ms: float
) -> None:
    """HTTP volume/latency plus explicit authentication and authorization denials."""
    metrics.record_metric(
        metrics.METRIC_HTTP_REQUESTS,
        labels={
            "method": request.method,
            "route": route,
            "status_class": f"{status_code // 100}xx",
        },
    )
    metrics.record_metric(
        metrics.METRIC_HTTP_DURATION_MS,
        value=duration_ms,
        unit="ms",
        labels={"method": request.method, "route": route},
    )
    if status_code == 401:
        metrics.record_metric(metrics.METRIC_AUTH_UNAUTHENTICATED, labels={"route": route})
        logger.warning(
            "request_unauthenticated",
            extra={"event": "auth.request_unauthenticated", "route": route, "status_code": 401},
        )
    elif status_code == 403:
        metrics.record_metric(metrics.METRIC_AUTHZ_FORBIDDEN, labels={"route": route})
        logger.warning(
            "request_forbidden",
            extra={"event": "authz.request_forbidden", "route": route, "status_code": 403},
        )


@app.middleware("http")
async def request_logging(request: Request, call_next):
    request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
    token = bind_request_id(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = round((time.perf_counter() - started) * 1000)
        logger.exception(
            "unhandled_request_error",
            extra={
                "event": "http.request_failed",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "duration_ms": duration_ms,
            },
        )
        _record_http_metrics(request, _route_label(request), 500, duration_ms)
        reset_request_id(token)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "request_id": request_id},
        )

    duration_ms = round((time.perf_counter() - started) * 1000)
    response.headers[REQUEST_ID_HEADER] = request_id
    route = _route_label(request)
    logger.info(
        "http_request_completed",
        extra={
            "event": "http.request_completed",
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "route": route,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    _record_http_metrics(request, route, response.status_code, duration_ms)
    reset_request_id(token)
    return response


@app.get("/")
def root() -> dict[str, str]:
    return {"service": settings.app_name, "status": "ok", "version": "0.1.0"}
