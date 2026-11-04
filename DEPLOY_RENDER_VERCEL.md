# Pramaan AI — Render + Vercel deployment and demo guide

Backend: Render (Docker, `backend/Dockerfile`). Frontend: Vercel (`frontend/`).
Database + private file storage: Supabase (PostgreSQL + pgvector, Storage bucket).
Model: Muse Spark via server-side Meta client. Observability: Langfuse (metadata only).

## 1. What is already wired

- `render.yaml` (repo root): Render Blueprint for the backend. The image runs
  `alembic upgrade head && uvicorn ... --port $PORT` on every deploy, with
  health check `GET /health/ready`.
- Auth: Supabase JWT (RS256/ES256) + workspace membership on every API route.
- Storage: `STORAGE_BACKEND=supabase` streams private PDFs through the
  authenticated `GET .../documents/{id}/file` endpoint. No public URLs.
- Langfuse: extraction/analysis/evaluation traces (metadata only) + aggregate
  evaluation scores on request. Inactive without keys; extraction never breaks.
- Guardrails: deterministic input/output screening on Investigate + citation
  verification on every claim. Evaluation: mocked (free) and live benchmark
  modes persisted to `evaluation_runs`/`evaluation_cases`.

## 2. Supabase setup (one time)

1. Create project. In SQL editor run: `create extension if not exists vector;`
2. Storage: create a **private** bucket, e.g. `pramaan-documents`.
3. Auth: create the demo user (email/password) in Authentication → Users.
   Copy the user's UUID (= JWT `sub`).
4. After the backend's first deploy (migrations applied), provision access —
   replace the three values:

```sql
insert into users (auth_subject, email, display_name, status)
values ('<SUPABASE-AUTH-USER-UUID>', 'demo@example.com', 'Demo Reviewer', 'ACTIVE')
returning id;
insert into workspaces (name, slug) values ('Demo Workspace', 'demo') returning id;
insert into workspace_members (workspace_id, user_id, role)
values ('<WORKSPACE-UUID>', '<USER-UUID>', 'OWNER');
```

5. Collect: pooled `DATABASE_URL` (pooler host, `?sslmode=require`),
   `SUPABASE_URL`, anon/publishable key (frontend), `service_role` key
   (backend only — never `NEXT_PUBLIC_`).

## 3. Backend on Render

Option A — Blueprint (recommended): Render → New → Blueprint → select this
repo. `render.yaml` creates `pramaan-ai-backend`. Set every `sync: false` var:

| Variable | Value |
|---|---|
| `APP_ENV` | `production` |
| `DATABASE_URL` | `postgresql+psycopg://…@<pooler-host>:6543/postgres?sslmode=require` |
| `MODEL_API_KEY` | Meta API key |
| `SUPABASE_URL` | `https://<ref>.supabase.co` |
| `SUPABASE_JWT_AUDIENCE` | `authenticated` |
| `STORAGE_BACKEND` | `supabase` |
| `SUPABASE_STORAGE_BUCKET` | bucket from §2 |
| `SUPABASE_SERVICE_ROLE_KEY` | backend only |
| `ALLOWED_ORIGINS` | exact Vercel origin, e.g. `https://pramaan-ai.vercel.app` |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | from §5 (or leave empty = tracing inactive) |
| `LANGFUSE_BASE_URL` | `https://cloud.langfuse.com` |
| `META_MODEL_ID` | `muse-spark-1.3` |
| `META_MAX_OUTPUT_TOKENS` | `8000` |
| `MAX_MODEL_CALLS_PER_REQUEST` | `4` |
| `DAILY_MODEL_CALL_LIMIT` | `20` |

Option B — existing service: confirm it builds branch `deploy/pramaan-ai-demo`
(or your merged branch), root/backend Dockerfile, and the same env vars.

Verify: `GET https://<backend>/health/live` → 200;
`GET https://<backend>/health/ready` → 200 (DB reachable + migrations applied).
`/docs` is disabled in production.

## 4. Frontend on Vercel

Root directory `frontend/`. Environment variables (Production + Preview):

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | `https://<render-backend>` (no trailing slash) |
| `NEXT_PUBLIC_SUPABASE_URL` | same Supabase URL |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | anon key |

Then set the backend `ALLOWED_ORIGINS` to the exact Vercel origin and redeploy
the backend (or wait for auto-deploy).

## 5. Langfuse setup (monitoring + scores)

1. Create account/project at `https://cloud.langfuse.com` (or self-host and set
   `LANGFUSE_BASE_URL`). Copy public + secret keys into Render (§3).
2. Trigger one extraction or Investigate run. In Langfuse → Traces you should
   see `pramaan.extraction.run` / `pramaan.analysis.run` with page/generation
   children carrying counts, durations and outcomes — no document text.
3. Scores: run Evaluation with “push aggregate scores” checked; the run report
   shows `scores_pushed_to_langfuse: true` and Langfuse → Scores lists
   `extraction.fact_f1`, `extraction.requirement_f1`, `extraction.quote_match_rate`.
4. If keys are absent or wrong, tracing logs `tracing.inactive` and the product
   works unchanged — verify via Render logs, not user-visible errors.

## 6. Local `.env` warning (do this today)

The repo-root `.env` currently contains **two** environments appended together
(`APP_ENV=local` … then `APP_ENV=production` with Supabase/Render values).
Production wins, so local runs and the test suite break
(`production requires durable gcs or supabase document storage`).

Fix (manual, your approval required — I did not touch `.env`):

1. Back up: `cp .env .env.render-backup`.
2. Delete everything from the second `APP_ENV=production` block to the end,
   keeping the local block (`APP_ENV=local`, localhost `DATABASE_URL`, …).
3. Keep production secrets only in the Render dashboard.

Tests: `cd backend && APP_ENV=test DATABASE_URL="postgresql+psycopg://pramaan:pramaan_local_only@127.0.0.1:5433/pramaan_test" .venv/bin/python -m pytest -q`
(requires local pgvector postgres on 5433, e.g. `docker compose up db`).

## 7. Three-minute demo script (maps to code)

- 0:00 Landing → “Try interactive demo” → sign in → pick workspace.
  (`frontend/src/app/page.tsx`, `onboarding/page.tsx`)
- 0:25 Dashboard: live counts from stored records. (`w/[workspaceId]/page.tsx`)
- 0:45 Upload the English sample PDF; watch the stage timeline.
  (`documents/upload`, `backend/app/services/document_service.py`)
- 1:10 Document detail: PDF preview, extracted text, run extraction; open a
  fact's quote → chunk excerpt. (`documents/[documentId]`, extraction panel)
- 1:40 Investigate: “What are the payment deadlines?” → Decision Packet with
  claim ledger, citations, unknowns. (`investigate`, `packets/[analysisId]`)
- 2:10 Changes: run detection → side-by-side conflict → resolve with note;
  Impact tab: compute depth-2 paths, validated vs candidate.
  (`changes`, `conflict_service.py`, `impact_service.py`)
- 2:35 Actions: propose from a packet draft → approve with rationale.
  (`actions`, `action_service.py`)
- 2:50 Evaluation: mocked run → F1 bars, failure explorer, export CSV.
  (`evaluation`, `evaluation_service.py`)
- 3:00 Settings: usage ledger, activity audit, privacy disclosure. Langfuse
  tab: trace + scores. (`settings`, `tracing.py`)

Budgets for the recording: extraction ≤ 4 calls/doc, Investigate 1 call,
evaluation mocked = 0 calls, live = 7 calls. Daily cap 20.

## 8. Honest limits of this demo

- Retrieval is lexical FTS (`lexical-fts-v1`); vector re-rank is not populated.
- Evaluation runs are installation-scoped (visible to every workspace member).
- Rate limiting is per-request cap + daily budget; no per-user/IP throttle —
  keep the demo behind login, do not publish the backend URL.
- No workspace creation/deletion UI; membership is admin SQL (§2).
- `SUPABASE_JWKS_URL` / `SUPABASE_PUBLISHABLE_KEY` in the sample `.env` are
  unused; the backend derives JWKS from `SUPABASE_URL`.
