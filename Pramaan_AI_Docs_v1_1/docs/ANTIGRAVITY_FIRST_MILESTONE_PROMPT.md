# Antigravity — First Milestone Prompt (M0 only)

> Paste this into Antigravity after opening the root folder of the initialized `pramaan-ai` repository and copying all documentation under `docs/`.

```text
You are the implementation agent for Pramaan AI. First read:
- docs/PRD.md
- docs/TECHNICAL_ARCHITECTURE.md
- docs/IMPLEMENTATION_PLAN.md
- docs/ANTIGRAVITY_IMPLEMENTATION_PROMPT.md

THIS TASK IS MILESTONE 0 ONLY: inspect and validate the starter foundation. Do not start document ingestion, fact extraction, the Evidence-to-Impact Graph, conflict detection, or new product pages yet.

Project definition: Pramaan AI is an Operational Change & Decision Integrity Engine. Its core differentiator is a persisted Evidence-to-Impact Graph connecting source passages, normalized facts/requirements, changes, conflicts, bounded impact paths and human-reviewed proposed actions. It must not become a generic RAG chatbot.

Important operating constraints:
1. The repository is already initialized or is in the process of being initialized. Do not run `git init`, overwrite repository history, force-push, reset hard, or delete user work.
2. Start with `git status --short --branch` and inspect the tree. Keep changes narrow.
3. Never print secret values. Never commit `.env`, API keys, database credentials, service-role keys, private documents, or generated sensitive traces.
4. Use local PostgreSQL + pgvector, FastAPI/Pydantic, Next.js/TypeScript, Meta Model API (server-side), and the documented configuration. Do not add Pinecone, a graph database, Redis, Celery, Kubernetes, GPU hosts, or paid services.
5. Do not create cloud resources or invoke a live model call without authorization. A live Meta request consumes credits. Inspect the smoke-test script now, but only execute it if I explicitly confirm or have already explicitly authorized one smoke call.
6. Don't claim tests or builds passed unless you ran them. For any unavailable check, provide the exact command and explain the blocker.

Steps:
A. Inspect the repository status and structure.
B. Read README, `.env.example`, `.gitignore`, Docker Compose, backend `pyproject.toml`, FastAPI app/config/database/health/meta-client modules, backend tests, frontend package/config, and `.github/workflows/ci.yml`.
C. Report what already works, what is incomplete, and any actual blockers. Do not rewrite code for style alone.
D. Verify that `.env` and frontend local environment files are ignored. Do not expose their contents.
E. Check Docker Compose configuration and provide commands to start PostgreSQL and verify `vector` extension.
F. Run available non-live tests and lint checks. If Python dependencies or frontend dependencies are already installed, use them; otherwise provide the installation commands. Do not install unrelated dependencies.
G. Start/check FastAPI if safe in the environment. Verify `/health/live` and `/health/ready` if the database is available. Check frontend lint/build if dependencies are available.
H. Inspect `backend/scripts/meta_smoke_test.py`, but do not call the live Meta API unless authorized.
I. Fix only concrete blockers preventing the foundation checks from passing. Add/update tests for every fix. Avoid unrelated changes.
J. Review the diff for secrets, accidental scope expansion, broken imports, untested behavior and inconsistent docs.

Report in this format:
1. Repo/status findings
2. Existing modules and what they do
3. Commands actually run and their results
4. Files changed and why
5. Remaining blockers and exact commands for the user
6. Go/no-go recommendation for Milestone 1 (database schema/migrations)

Stop after the report. Wait for my explicit approval before implementing Milestone 1.
```

## Expected commands (for the human developer)

From the repo root, these are the typical checks. Run from the correct directory based on the actual starter files:

```bash
git status --short --branch
docker compose up -d db
docker compose ps
```

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e '.[dev]'
pytest -q
ruff check app tests scripts
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
curl http://127.0.0.1:8000/health/live
curl http://127.0.0.1:8000/health/ready
```

Frontend:

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
npm run lint
npm run build
```

Do not blindly run all commands at once. Read the project files first, start services stepwise, and resolve the first failure before proceeding.
