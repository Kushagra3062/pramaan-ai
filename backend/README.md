# Backend foundation

See root `README.md` for exact setup commands.

- `app/main.py`: FastAPI app, CORS, request IDs, structured request logging.
- `app/core/config.py`: validated environment configuration.
- `app/core/meta_client.py`: server-only Meta Model API client factory.
- `app/services/meta_service.py`: single-call smoke service.
- `app/db/engine.py`: SQLAlchemy engine and readiness check.
- `app/api/routes/health.py`: liveness and readiness checks.
- `app/api/routes/dev.py`: local-only model smoke endpoint; omitted in production.
- `scripts/meta_smoke_test.py`: CLI smoke test.

This is not yet a full product API. Do not expose upload, Q&A, or action routes until authentication, authorization, file validation, budget enforcement, and tests are implemented.
