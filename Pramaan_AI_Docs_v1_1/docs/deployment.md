# Restricted deployment runbook

## Free-tier demonstration target (Render + Supabase + Vercel)

Use a Render Docker Web Service for `backend/`, Vercel Hobby for `frontend/`, and a
Supabase PostgreSQL project plus a **private** Storage bucket named `pramaan-documents`.
Set `STORAGE_BACKEND=supabase`, `SUPABASE_STORAGE_BUCKET=pramaan-documents`, and provide
`SUPABASE_SERVICE_ROLE_KEY` only to Render's secret environment (never Vercel or a
`NEXT_PUBLIC_*` value). The application stores only opaque workspace/document keys in
PostgreSQL and uses backend-mediated reads/writes; do not make the bucket public.

Render builds the existing `backend/Dockerfile` and honours its `PORT` command. Run
`alembic upgrade head` as a separately reviewed one-off command against the confirmed
Supabase database before starting the service; never place migration execution in the
container startup command. Render Free instances can sleep and have limited CPU/RAM:
retain the existing PDF/page/model-call limits and use only small synthetic demo PDFs.

Vercel needs `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_SUPABASE_URL`, and
`NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`. Render needs the matching `SUPABASE_URL`,
`SUPABASE_JWT_AUDIENCE`, `SUPABASE_SERVICE_ROLE_KEY`, database URL with TLS,
`ALLOWED_ORIGINS` set to the exact Vercel HTTPS URL, plus model/Langfuse settings as
applicable. After deployment, test `/health/live`, `/health/ready`, authenticated
workspace discovery, one synthetic upload, and authorized document retrieval; do not
trigger live extraction merely as a deployment probe.

Pramaan AI enforces Supabase JWT authentication and workspace-membership/role authorization on every document and extraction route. It also enforces an installation-wide atomic daily model-call budget. Authentication and budget controls are not substitutes for durable private PDF storage, external request-abuse controls, and operational monitoring: confirm `STORAGE_BACKEND=gcs` or documents are lost between Cloud Run instances. Until all release gates below are independently verified, keep Cloud Run private behind an IAM-restricted demo boundary and use only synthetic/sample documents.

## Backend: Cloud Run

Build the backend image from `backend/`. It listens on Cloud Run's injected `PORT` (default `8080`) and exposes `/health/live` and `/health/ready`. Run migrations as a separate, explicit release step; application startup never migrates the database.

Required production configuration:

- `APP_ENV=production`
- `DATABASE_URL`: Supabase PostgreSQL SQLAlchemy URL with `sslmode=require`
- `MODEL_API_KEY`: Secret Manager reference, never an image build argument or tracked file
- `SUPABASE_URL`: HTTPS project URL used for JWKS issuer/audience verification
- `ALLOWED_ORIGINS`: exact HTTPS Vercel origin(s), comma separated
- `STORAGE_BACKEND=gcs`, `GCS_BUCKET_NAME`, and optionally `GCS_PROJECT_ID`

Optional but recommended production configuration:

- `LANGFUSE_ENABLED=true`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` (Secret Manager references; see below)

Grant the Cloud Run service account only object read/write/delete access to its dedicated private GCS bucket. Enable uniform bucket-level access and public-access prevention. Use Application Default Credentials; do not upload service-account JSON or set it as an environment variable.

Before shifting traffic, run `alembic upgrade head` against the intended managed database using a one-off controlled job, then check `alembic current`. Do not run migrations automatically on application startup or against a database whose target has not been confirmed.

Verify `/health/live` first, then `/health/ready`. A readiness failure means the database/TLS configuration is wrong; do not expose traffic until corrected.

## Frontend: Vercel

Set only `NEXT_PUBLIC_API_BASE_URL` to the restricted HTTPS backend URL. Never set model credentials, database URLs, GCS credentials, or service-role secrets in `NEXT_PUBLIC_*` variables. Configure the matching Vercel origin in `ALLOWED_ORIGINS`.

The browser sends the Supabase access token as `Authorization: Bearer <jwt>`; the backend verifies it against Supabase JWKS, requires `exp`, and checks workspace membership and role before any read or write.

## Monitoring and observability

No monitoring resource is created by application code. Everything below is emitted as structured JSON on stdout (Cloud Run captures it automatically) and is turned into Cloud Monitoring log-based metrics, dashboards and alerts during release. This milestone did not call Google Cloud APIs and did not create dashboards or alert policies.

### Structured log fields

Every application log line is one JSON object. Stable fields:

| Field | Meaning |
| --- | --- |
| `severity`, `timestamp`, `service`, `environment` | Cloud Logging standard fields (severity is mapped from the Python level) |
| `event` | Stable event name, e.g. `http.request_completed`, `extraction.run_finished`, `extraction.chunk_failed`, `extraction.response_rejected`, `extraction.persisted`, `auth.request_unauthenticated`, `authz.request_forbidden`, `usage.limit_rejected`, `health.db_failed`, `tracing.inactive`, `tracing.failed`, `metric` |
| `request_id` | HTTP request id (from `X-Request-ID` or generated); injected into every log line of a request |
| `extraction_run_id` | Extraction run id; matches `audit_events.request_id` and `model_usage` rows |
| `page_number`, `document_id`, `workspace_id` | Correlation ids (logs only, never metric dimensions) |
| `model_id`, `prompt_version` | Model and prompt provenance |
| `request_id` on model events | Meta request id (e.g. `chatcmpl-...`) |
| `input_tokens`, `output_tokens`, `reasoning_tokens`, `latency_ms`, `finish_reason` | Model execution metadata |
| `reason` / `error_category`, `status` | Typed failure category and processing status |
| `schema_error_count`, `schema_errors`, `schema_errors_truncated` | Sanitized validation diagnostics: `loc` and `type` only, never values |
| `evidence_failure_reasons` | Counts per typed evidence-verification failure; never quotes |

Privacy guarantees enforced by the log formatter itself: field names matching the credential/content policy (`api_key`, `authorization`, `*_token`, `*_secret`, `database_url`, `source_quote`, `*_text`, `*_prompt`, `*_response`, `content`, `prompt`, ...) are replaced with `[REDACTED]`, and string values are truncated to 512 characters. Prompts, PDF text, transcriptions, quotations, raw model responses and rejected values are never logged by any code path.

### Metric catalog

Metric samples are log lines with `event="metric"`, `metric_name`, `metric_value`, `metric_unit` and a bounded `labels` object. Metric label keys are restricted to a fixed allowlist, so workspace, user, document, chunk and run identifiers can never become dimensions.

| Metric name | Unit | Labels | Meaning |
| --- | --- | --- | --- |
| `api.requests` | count | `method`, `route`, `status_class` | HTTP volume; derive 4xx/5xx rates from `status_class` |
| `api.request.duration_ms` | ms | `method`, `route` | HTTP latency |
| `auth.unauthenticated` | count | `route` | 401 responses (missing/invalid/expired token, unknown identity) |
| `authz.forbidden` | count | `route` | 403 responses (not a member / insufficient role) |
| `documents.uploads` | count | `outcome` (`success`, `failed`, `failure`, `rejected`, `duplicate`) | Upload and PDF-processing outcome |
| `documents.ocr.pages` | pages | `outcome`, `mode` | OCR/Vision pages produced, or the failed attempt |
| `extraction.runs` | count | `status` (`COMPLETED`, `PARTIAL`, `FAILED`, `ABORTED`) | Document-level extraction outcomes |
| `extraction.duration_ms` | ms | `status` | Extraction run duration |
| `extraction.pages` | count | `status` (`PROCESSED`, `SKIPPED`, `FAILED`, `NOT_PROCESSED`) | Page outcomes |
| `extraction.schema_rejections` | count | `reason` | Model output rejected by the strict schema |
| `extraction.evidence_failures` | count | `reason` (`QUOTE_NOT_IN_PAGE`, `AMBIGUOUS_CHUNK_MATCH`, `FACT_VALUE_NOT_IN_EVIDENCE`, ...) | Deterministic evidence-verification failures |
| `model.requests` | count | `outcome` (`success`, `rate_limited`, `timeout`, `connection_error`, `status_error`, `api_error`), `model_id` | Meta request outcomes |
| `model.tokens` | tokens | `direction` (`input`, `output`, `reasoning`), `model_id` | Token usage actually reported by the API; unreported usage is not counted |
| `model.latency_ms` | ms | `model_id` | Model call latency |
| `database.readiness` | count | `outcome` (`ok`, `failed`) | `/health/ready` database probe |
| `usage_limit.rejections` | count | `limit` (`per_request`, `daily`) | Requests refused before any model call was sent |
| `langfuse.failures` | count | `stage` (`init`, `run_start`, `generation_start`, `flush`, ...) | Tracing failures (never affects extraction) |

`route` is always the FastAPI route template (e.g. `/api/v1/workspaces/{workspace_id}/documents/{document_id}/extract`), never the raw path, and `unmatched` for unknown paths.

### Creating log-based metrics

For each row above, create a Cloud Monitoring log-based metric with:

- Filter: `jsonPayload.event="metric" AND jsonPayload.metric_name="<metric name>"`
- Value: `jsonPayload.metric_value` (distribution metrics: use the same value extractor and treat `api.request.duration_ms` and `model.latency_ms` as distributions)
- Labels: `jsonPayload.labels.<label>` for each label in the table

Example filter for extraction outcomes:

```
resource.type="cloud_run_revision"
resource.labels.service_name="pramaan-backend"
jsonPayload.event="metric"
jsonPayload.metric_name="extraction.runs"
```

### Dashboard

One overview dashboard with four rows:

1. Traffic and health: `api.requests` by `status_class`, `api.request.duration_ms` p50/p95, `database.readiness`.
2. Extraction pipeline: `extraction.runs` by `status`, `extraction.pages` by `status`, `extraction.duration_ms`, `extraction.schema_rejections`, `extraction.evidence_failures`.
3. Model spend: `model.requests` by `outcome`, `model.tokens` by `direction`, `model.latency_ms` p95, `usage_limit.rejections`.
4. Access: `auth.unauthenticated`, `authz.forbidden`.

### Alert recommendations

| Condition | Suggested threshold | Why |
| --- | --- | --- |
| `api.requests` with `status_class="5xx"` rate | > 1% of requests over 5 min | Backend regression or database outage |
| `api.request.duration_ms` p95 on the extract route | > 60 s for 10 min | Model latency or DB lock contention |
| `extraction.runs` with `status="FAILED"` | >= 3 in 15 min | Prompt/model drift or schema breakage |
| `extraction.schema_rejections` | > 0 in 1 h | Model output violating the schema contract (prompt regression) |
| `model.requests` with `outcome="rate_limited"` | > 0 in 5 min | Provider quota pressure |
| `extraction.evidence_failures` with `reason="QUOTE_NOT_IN_PAGE"` | ratio > 20% of proposals | Model copying versus paraphrasing evidence |
| `database.readiness` with `outcome="failed"` | > 0 | Cloud Run will fail readiness; traffic must not shift |
| `auth.unauthenticated` | sudden spike | Expired JWKS, wrong `SUPABASE_URL`, or credential stuffing |
| `usage_limit.rejections` | steady increase | Documents too large for the per-request call cap; cost pressure |
| `langfuse.failures` | > 0 | Tracing degraded; extraction is unaffected but observability is lost |

## Daily model call budget

`DAILY_MODEL_CALL_LIMIT` (default 20) caps outbound Meta requests for the whole installation per UTC calendar day, shared by every workspace and user. It is independent of `MAX_MODEL_CALLS_PER_REQUEST`, which caps how many calls one document may use; the per-request cap is checked first (HTTP 413) and the daily budget second (HTTP 429).

Behaviour:

- Capacity is reserved atomically in PostgreSQL (`model_call_budget`, one row per UTC day) **before** the first model call. A run is admitted only if its entire planned call count fits; otherwise the request is rejected and nothing is extracted.
- One slot is consumed immediately before each real outbound request: one per canonical chunk in structured extraction, and one per page in `meta_vision`/`compare` OCR.
- Intentionally not charged: local Tesseract OCR, offline evaluation, unit tests using fake extractors or fakes without the `calls_model_provider` marker, the local-only `/api/v1/dev` smoke-test route (mounted only for `local`/`development`/`test`), and the manually gated `scripts/diagnose_meta_extraction.py` / `scripts/meta_smoke_test.py` operator diagnostics (one explicit request each, no persistence). These are operator-only paths outside production traffic; they are listed here so the exclusion stays deliberate.
- Reserved-but-unattempted slots are released when the model phase ends, on success or failure. Slots abandoned by a crashed process stay counted for that day, so the budget can be under-used but never over-used.
- The budget resets at 00:00 UTC because the next day is a new row; no scheduled job is involved.
- Provider retries cannot bypass accounting: `get_meta_client()` sets `max_retries=0`, and the project's no-retry policy is preserved, so no request is silently repeated.

Rejection signals: HTTP 429 with a `detail` body and a `Retry-After` header (seconds until the UTC reset), log event `usage.limit_rejected` with `limit="daily"`, `requested_calls`, `remaining_calls`, `daily_model_call_limit`, `budget_date`, and metric `usage_limit.rejections{limit="daily"}`. No extraction run, page, fact, `ModelUsage` or `AuditEvent` row is created.

Operator procedure when the budget is exhausted:

1. Confirm the affected requests with `usage_limit.rejections{limit="daily"}` and the `usage.limit_rejected` log entries.
2. Inspect the counter: `SELECT budget_date, reserved_calls, updated_at FROM model_call_budget ORDER BY budget_date DESC LIMIT 2;`
3. Wait for the UTC reset, or raise `DAILY_MODEL_CALL_LIMIT` deliberately — it is a cost safety control, so do not set it beyond what the model budget can fund.
4. Only after a crash investigation, and only when it is established that no request was sent, reset the row manually: `UPDATE model_call_budget SET reserved_calls = 0 WHERE budget_date = CURRENT_DATE;`. The application never restores abandoned capacity automatically.

## Langfuse (LLM observability)

Langfuse is the required LLM observability component. It is metadata-only by design:

- One Langfuse trace per extraction run (`pramaan.extraction.run`), one span per page (`pramaan.extraction.page`), one generation per actual Meta request (`muse-spark.extraction`).
- Attributes are restricted to identifiers, counts, latency, tokens, model id, prompt version, request id, finish reason and typed outcomes. There is no API in `app/core/tracing.py` that accepts prompts, source text, quotations or model responses, so content cannot be attached even by mistake.
- Correlation: the trace metadata carries `extraction_run_id` (same value as `audit_events.request_id` and the `model_usage` rows), and each generation carries Meta's request id.

Configuration (Secret Manager in production, `.env` locally):

- `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` — required to activate tracing; never commit them
- `LANGFUSE_BASE_URL` — `https://cloud.langfuse.com` (EU) or `https://us.cloud.langfuse.com` (US)
- `LANGFUSE_ENABLED` — `false` disables tracing even when keys exist

Failure isolation: with missing or partial credentials, `LANGFUSE_ENABLED=false`, an SDK import error, or any runtime failure, tracing stays inactive or is disabled for the process after one warning and one `langfuse.failures` sample. Extraction, evidence verification, persistence, `ModelUsage` and `AuditEvent` writes are unaffected and never retried, so tracing cannot produce duplicate usage rows. A run flushes its trace once at the end (bounded by a 5 second client timeout) and the app flushes on shutdown.

Operational check after deployment: run one extraction on a sample document, then confirm the matching trace exists in Langfuse with the same `extraction_run_id` and a generation count equal to the number of eligible chunks. Do not treat tracing as verified until that check passes with real credentials.

## Release gate

Before any public launch, independently verify abuse/rate controls (the per-request call cap and daily budget are not a general request rate limit), durable private storage, alerting based on the metrics above, and the disposable CI database. Until then, the only allowed deployment posture is private restricted demo access.
