# Pramaan AI — implementation kickstart

This is the **Phase 0 foundation** for the new Pramaan AI repository. It deliberately does not implement the whole product yet. It gives you a small, runnable foundation to prove configuration, health checks, database connectivity, and one Meta Model API call before adding ingestion and retrieval.

## Stack in this starter

- Python 3.11+
- FastAPI + Pydantic Settings
- SQLAlchemy 2 + psycopg 3 for PostgreSQL connectivity
- Meta Model API via the OpenAI Python SDK / Responses API
- Pytest + HTTPX for initial checks
- Docker Compose for local PostgreSQL with pgvector available
- Next.js will be created in `frontend/` in Phase 0 (commands below)

Production target: Next.js on Vercel, FastAPI on Cloud Run, Supabase Storage + PostgreSQL/pgvector, Langfuse for AI traces, Cloud Logging/Monitoring for runtime telemetry.

## 0. Create the GitHub repo

Create an **empty** GitHub repository named `pramaan-ai` under your account. Don't create it as a fork. Then in this folder:

```bash
git init -b main
git add .
git commit -m "chore: bootstrap Pramaan AI foundation"
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/pramaan-ai.git
git push -u origin main
git switch -c feat/platform-foundation
```

If you prefer to create the GitHub repository after trying locally, keep the initial commit local and add the remote later.

## 1. Prerequisites (macOS)

Install/check:

```bash
brew install python@3.11
python3.11 --version
docker --version
docker compose version
node --version
npm --version
```

Install Docker Desktop if `docker` is unavailable. Install Node.js LTS if Node/npm are missing.

## 2. Configure environment

```bash
cp .env.example .env
```

Put your Meta key in `.env` as `MODEL_API_KEY=...`. Never commit `.env`. Confirm API access and billing/credit terms before running live model calls. The smoke test consumes one model request.

## 3. Start local PostgreSQL with pgvector

```bash
docker compose up -d db
docker compose ps
```

The local DB is disposable development data; it is not production. In `.env`, the default `DATABASE_URL` points to it.

## 4. Start the API

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e '.[dev]'
cd ..
```

Run the API (from `backend/` with the virtualenv activated):

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Verify:

```bash
curl http://127.0.0.1:8000/health/live
curl http://127.0.0.1:8000/health/ready
```

`/health/ready` checks PostgreSQL connectivity. If it fails, inspect `.env`, Docker status, and the connection URL.

## 5. Test Meta Model API connectivity

In a separate terminal:

```bash
cd pramaan-ai/backend
source .venv/bin/activate
python scripts/meta_smoke_test.py
```

Expected result: the model replies with `READY` (possibly with surrounding whitespace). If it fails, copy the status/error message but **never share the API key**. Verify the key, model access, billing/credits, rate limit, and Meta API dashboard.

## 6. Run tests and lint

```bash
cd backend
source .venv/bin/activate
pytest -q
ruff check app tests scripts
```

## 7. Start the included frontend foundation

The ZIP includes a minimal Next.js page that displays the product positioning and checks the API health endpoint. Install and start it:

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

Open `http://localhost:3000`. It should show API connected when the backend is running. The page's main action is an intentionally non-functional placeholder until document ingestion is implemented.

Only the public backend URL belongs in a `NEXT_PUBLIC_` variable. Never put Meta keys, Supabase service-role keys, database passwords, or Langfuse secret keys there.

## 8. CI pipeline

The included `.github/workflows/ci.yml` runs backend lint/tests and frontend lint/build on pushes and pull requests. The frontend job uses `npm install` because this initial kit deliberately leaves lockfile generation to your local Node/npm toolchain. After the first `npm install`, commit the generated `frontend/package-lock.json`; then you can switch CI to `npm ci`.

## 9. First implementation milestone

Do not start with the dashboard or multi-agent system. First prove:

1. API starts.
2. DB readiness check passes.
3. Meta smoke test succeeds.
4. Tests and lint pass.
5. Frontend starts and can reach the backend health endpoint.
6. `.env` is ignored by Git and no secrets appear in `git status` or `git diff`.

Only then implement migrations and core tables, followed by file upload + storage, extraction, chunks, embeddings, hybrid retrieval, evidence-grounded Q&A, conflict detection, and approval workflow.

See `docs/implementation-roadmap.md` for sequencing and acceptance criteria, and `infra/cloud-run.md` for deployment safety notes.
