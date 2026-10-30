---
title: "Pramaan AI — Implementation Plan"
version: "1.1"
status: "Execution plan"
updated: "2026-10-29"
---

# Pramaan AI — Step-by-Step Implementation Plan

**Purpose:** Execute PRD v1.1 and Technical Architecture v1.1 as a working, testable, publicly inspectable hackathon submission.

> **Single source of truth:** `PRD.md` defines product behavior and scope. `TECHNICAL_ARCHITECTURE.md` defines implementation boundaries and contracts. This plan sequences the work. If they conflict, stop and document the discrepancy instead of silently improvising.

## 0. Product and delivery constraints

### Core product
Pramaan AI is an **Operational Change & Decision Integrity Engine**. Its core differentiator is a persisted **Evidence-to-Impact Graph**, not a chat interface. The graph connects:

1. Source documents and page/chunk evidence.
2. Normalized facts and obligations.
3. Version changes and candidate contradictions.
4. Impact paths through explicitly represented relationships.
5. Human-reviewed proposed actions and decisions.

Muse Spark interprets multimodal/contextual evidence and explains candidate impacts. Deterministic services handle access control, normalization, comparisons where possible, graph traversal bounds, citation ID checks, state transitions, idempotency, and audit persistence.

### Hard constraints
- New repository; do not fork or overwrite the original Kochi-Metro repository.
- Public repository for the hackathon; no secrets, private documents, or unlicensed assets.
- Use Meta Model API meaningfully; model credentials remain server-side.
- Current target stack: Next.js + TypeScript; FastAPI + Python; PostgreSQL + pgvector; Supabase private Storage; Meta Model API / Muse Spark; GCP Cloud Run; Vercel; Cloud Logging/Monitoring; Langfuse with redaction; GitHub Actions.
- Avoid Pinecone, a separate graph database, Redis, Celery, Kubernetes, paid domains, GPU VMs, and arbitrary autonomous tools for the MVP unless a measured blocker justifies a change.
- Target free allowances, but never promise an unconditional $0 bill. Verify provider eligibility, quotas, terms, regions, payment requirements, and usage dashboards.
- Do not reuse existing project code/data/assets unless rights to use them are clear. Track new work in commits and PRs.
- Never invent accuracy numbers. Use labelled evaluation data and report actual measurements with sample counts.

## 1. Git strategy and team workflow

The user has already downloaded the starter ZIP and plans to initialize the new repository and push the initial commit. **Do not run `git init`, replace the repository, or rewrite history if Git is already initialized.** First inspect the workspace.

Recommended branch names:
- `feat/platform-foundation`
- `feat/database-schema`
- `feat/document-ingestion`
- `feat/hybrid-retrieval`
- `feat/fact-extraction`
- `feat/evidence-impact-graph`
- `feat/conflict-detection`
- `feat/decision-packet-ui`
- `feat/evaluation-observability`
- `fix/release-blockers`

Every milestone should finish with a clean diff, passing relevant checks, and a concise commit. Do not add secrets or actual test PDFs containing private information.

## 2. Milestone 0 — Inspect and validate the starter foundation

### 2.1 Inspect before editing
Run:

```bash
git status --short --branch
find . -maxdepth 4 -type f | sort
```

Read `README.md`, `.env.example`, `.gitignore`, `docker-compose.yml`, `backend/pyproject.toml`, `backend/app/main.py`, all starter health/config/model modules, `frontend/package.json`, and `.github/workflows/ci.yml`.

### 2.2 Local configuration

```bash
cp .env.example .env
```

Edit `.env` locally. Confirm it is ignored:

```bash
git check-ignore .env
```

Do not place the Meta API key in any frontend variable or tracked file. Keep `frontend/.env.local` for non-secret frontend configuration only.

### 2.3 Start database and verify extension

```bash
docker compose up -d db
docker compose ps
docker compose exec db psql -U pramaan -d pramaan_dev \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"
docker compose exec db psql -U pramaan -d pramaan_dev \
  -c "SELECT extname FROM pg_extension WHERE extname = 'vector';"
```

### 2.4 Backend checks

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

### 2.5 One Meta smoke test

After confirming the environment variable and account access:

```bash
cd backend
source .venv/bin/activate
python scripts/meta_smoke_test.py
```

Run one live request initially; avoid retry loops. Inspect the returned error and Meta usage/billing if it fails. Do not proceed as though model integration works until an actual successful response is observed.

### 2.6 Frontend checks

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

Open `http://localhost:3000`, verify the API connection, then run:

```bash
npm run lint
npm run build
```

### Exit criteria
- [ ] Starter files understood and no unnecessary rewrite made.
- [ ] `.env` ignored by Git; no secrets in `git status` or tracked files.
- [ ] Local PostgreSQL starts and pgvector extension exists.
- [ ] `/health/live` and `/health/ready` pass.
- [ ] One Meta smoke request works or its exact failure is isolated and documented.
- [ ] Frontend runs and backend tests/lint/build pass.
- [ ] CI runs on the current branch.

Do not begin ingestion until this gate passes.

## 3. Milestone 1 — Documentation and technical decision lock

Create/commit the project documentation inside `docs/`:

```text
docs/
  PRD.md
  TECHNICAL_ARCHITECTURE.md
  IMPLEMENTATION_PLAN.md
  ANTIGRAVITY_IMPLEMENTATION_PROMPT.md
  assets/
```

Confirm all asset links resolve. Add a `README.md` section for product pitch, key architecture, local setup, test commands, current limitations, and “Built During This Hackathon” (fill only with actual completed contributions).

Create an ADR for any decision that deviates from v1.1. Do not update the PRD to fit accidental code shortcuts; explain and justify changes explicitly.

## 4. Milestone 2 — Database schema and migration baseline

### Implement in this order
1. Add Alembic configuration and use the application SQLAlchemy settings.
2. Define SQLAlchemy models and Pydantic schemas for workspace, member, document, page, chunk, fact, requirement, graph node, impact edge, analysis, claim, evidence link, conflict, action item, audit event, model usage, evaluation dataset/case/run/result.
3. Add the pgvector extension through a migration or documented DB bootstrap step appropriate to local/Supabase.
4. Create migrations in logical groups: core documents; graph/intelligence; audit/evaluation.
5. Add FK, check, uniqueness and useful indexes. Use UUID primary keys where appropriate.
6. Ensure cross-workspace edges/relations are impossible through service validation and, where feasible, DB constraints/RLS.
7. Add repository tests against a disposable local database.

### Graph contracts
Use relational Postgres tables for `graph_nodes` and `impact_edges`; no separate graph database in the MVP.

Node types (initial): `DOCUMENT`, `PAGE`, `CHUNK`, `FACT`, `REQUIREMENT`, `CHANGE`, `CONFLICT`, `ACTION`, optionally `DEPARTMENT`.

Relationship types (initial): `DERIVED_FROM`, `SUPPORTED_BY`, `CONTRADICTS`, `SUPERSEDES`, `AFFECTS`, `ASSIGNED_TO`, `PROPOSED_FROM`.

Every edge must carry `workspace_id`, source/target IDs, relation type, provenance (`DETERMINISTIC`, `MODEL_PROPOSED`, `HUMAN_CONFIRMED`), verification status, and created/updated timestamps. Add evidence references for material claims and edges.

### Exit criteria
- Clean DB can be created from migrations.
- Migration can be rerun safely as designed.
- FK and uniqueness constraints behave as expected.
- A cross-workspace relation is rejected.
- No graph edge can be created without existing endpoints.
- Reapplying the same seed input is idempotent.

## 5. Milestone 3 — Document upload, storage and ingestion

### Service modules
- `StorageService` / provider adapter: put, get, signed URL, delete.
- `DocumentService`: workspace ownership, create/list/detail/delete/reprocess.
- `IngestionService`: validate, extract, OCR fallback, chunk, embed, persist, update state.
- `PdfExtractionService`: PyMuPDF native extraction and Tesseract fallback.
- `ChecksumService`: SHA-256 and duplicate detection.
- `ProcessingStatusService`: transition validation and persisted stage/status updates.

### API routes
Implement the route inventory from the technical architecture. At minimum:

```text
POST   /api/v1/documents
GET    /api/v1/documents
GET    /api/v1/documents/{document_id}
DELETE /api/v1/documents/{document_id}
POST   /api/v1/documents/{document_id}/reprocess
GET    /api/v1/documents/{document_id}/pages
```

### Processing logic
1. Authorize before processing.
2. Validate content type/signature; do not trust filename only.
3. Enforce 10 MB and 20-page limits for MVP.
4. Hash content; detect existing document by workspace and hash.
5. Store the original in a private object bucket (local adapter during local development).
6. Persist `UPLOADED` and transition to `PROCESSING`.
7. Extract text page by page. If text coverage is insufficient, run OCR for that page.
8. Persist page text, source offsets if available, extraction method, and warnings.
9. Chunk without losing page/source mapping.
10. Generate embeddings and persist chunk + model/version/dimension metadata.
11. Create initial `DOCUMENT -> PAGE -> CHUNK` provenance graph records deterministically.
12. Finish as `READY`, `READY_WITH_WARNINGS`, or `FAILED`.
13. Return useful status without exposing raw server exceptions.

For MVP, run bounded processing synchronously behind the service interface. Persist stage states and use page/file caps. Do not pretend an in-process background task is a durable queue. If a hard timeout is observed, document the evidence before introducing Cloud Tasks/worker complexity.

### Edge cases to test
- Wrong extension/non-PDF bytes; encrypted PDF; corrupt PDF; zero pages; oversized document; over-page-limit; duplicate SHA; empty scanned pages; partial OCR; unsupported/poor OCR language; timeout; storage failure; DB failure; duplicate/replayed upload; deleted file; missing object; failed retry.

### Exit criteria
An authorized user uploads a permitted public/synthetic PDF; it is stored privately; page text and chunks are persisted with source page mappings; bad input is rejected predictably; retry is idempotent; deletion removes related searchable data and graph edges according to retention policy.

## 6. Milestone 4 — Embeddings and hybrid retrieval

### Technical choices
- Postgres + pgvector, no Pinecone for MVP.
- Pin one multilingual embedding model and its version/dimension.
- Use PostgreSQL full-text search as the lexical path.
- Add an HNSW index only after confirming vector type/dimension and benchmarking query behavior.
- Use authorized workspace/document filters in every retrieval function.

### Modules
- `EmbeddingService`
- `VectorSearchRepository`
- `LexicalSearchRepository`
- `HybridRetrievalService`
- candidate deduper/merger
- optional bounded reranker only if time/measurements justify it

### Retrieval algorithm
1. Validate selected document IDs and workspace membership.
2. Generate query embedding.
3. Run vector search and lexical search under identical access filters.
4. Merge candidates; deduplicate by chunk ID.
5. Use simple rank fusion initially (e.g. reciprocal rank fusion) rather than unvalidated score normalization.
6. Apply a small top-k and token budget.
7. Return evidence objects with chunk ID, document ID/title, page number, exact source text, match method/rank, and content hash/version.
8. Emit retrieval metrics and a trace span without logging full sensitive content.

### Exit criteria
On labelled test questions, expected evidence is found; direct document filters work; duplicate chunks are removed; no cross-workspace leakage occurs; no vector dimension mismatch; retrieval outputs keep accurate page references.

## 7. Milestone 5 — Fact extraction and Evidence-to-Impact Graph

This is the core differentiator. Build it before fancy visualization.

### `FactExtractionService`
Extract candidate facts as schema-validated JSON:

```json
{
  "fact_type": "DEADLINE",
  "raw_value": "15 November 2026",
  "normalized_value": "2026-11-15",
  "source_document_id": "...",
  "source_page": 4,
  "source_quote": "Applications must be submitted by...",
  "provenance": "MODEL_PROPOSED",
  "verification_status": "NEEDS_REVIEW"
}
```

This is illustrative. Implement actual Pydantic enums and required fields. Model output must never be accepted without schema and source validation.

Initial types: dates/deadlines, monetary amounts/currency, identifiers, obligations, approval requirements, owner/department, policy clauses.

### `GraphBuilderService`
- Create deterministic document/page/chunk nodes from persisted records.
- Create fact/requirement nodes for schema-valid extractions.
- Create `SUPPORTED_BY`/`DERIVED_FROM` edges only when the source passage exists.
- Use `MODEL_PROPOSED` for semantic relation proposals from Muse Spark.
- Deduplicate nodes/edges with explicit natural keys or idempotency keys.
- Keep a version/corpus identifier so reprocessing can supersede prior derived records safely.

### `ChangeDetectionService`
- Compare compatible normalized facts only.
- Normalize dates, currencies and units before comparison.
- Categorize `ADDED`, `REMOVED`, `MODIFIED`, `UNCHANGED`, `UNCERTAIN`.
- Store both previous and current evidence references.
- Do not assume a newer document is authoritative solely because it is newer.

### `ConflictDetectionService`
- Deterministically compare same-type facts with comparable scope.
- Create candidates for incompatible values.
- Use Muse Spark to interpret contextual cases where two facts may not truly conflict.
- Persist conflict status and both sides’ evidence.
- Never silently resolve conflicts automatically.

### `ImpactAnalysisService`
- Traverse only allowlisted edge types.
- Enforce workspace scope, maximum path depth (e.g. 3), and maximum returned nodes/edges.
- Exclude unverified model-proposed edges from “verified impact” paths unless explicitly shown as hypotheses.
- Return each path with node/edge IDs and evidence references.
- Have Muse Spark explain only the returned paths; it may not invent new graph facts in the explanation.

### Exit criteria
For a seeded sample corpus, the system detects at least one labelled change and one labelled conflict, displays both sources, traverses a known seeded impact path, identifies uncertain/no-path cases correctly, and does not duplicate graph entities on rerun.

## 8. Milestone 6 — Investigation and Decision Packet

### Analysis pipeline
1. Authenticate and authorize workspace/documents.
2. Validate query and requested scope.
3. Create `analysis` record and correlation ID.
4. Retrieve candidate evidence using hybrid search.
5. Load normalized facts, known conflicts, and graph paths restricted to selected documents/workspace.
6. Call Muse Spark using a versioned prompt and a bounded structured response contract.
7. Parse with Pydantic; allow at most one controlled repair attempt if the response is malformed.
8. Verify all evidence IDs refer to authorized retrieved passages.
9. Validate claim support deterministically where possible; mark uncertain/unsupported claims and require abstention/review if necessary.
10. Save claims, evidence links, result JSON, model usage, latency, prompt/model version, and guardrail outcome.
11. Return a Decision Packet with findings, evidence, changes, conflicts, impact paths, proposed actions, open questions, and `needs_human_review`.

No hidden “chain-of-thought” should be requested or logged. Store a concise rationale tied to the evidence, not private reasoning traces.

### API baseline
Use versioned routes from the architecture; keep request/response schemas typed and error responses consistent. For example:

```text
POST /api/v1/investigations
GET  /api/v1/investigations/{analysis_id}
GET  /api/v1/investigations/{analysis_id}/evidence
```

### Exit criteria
A user asks a cross-document question and receives a validated Decision Packet with source-page links; nonexistent IDs are rejected; unsupported queries trigger an explicit abstention; rerun results are traceable to prompt/model versions.

## 9. Milestone 7 — Conflict Center, action approval, audit

### Conflict resolution
- Display both candidate facts and both source excerpts/pages.
- State the conflict type and what remains unknown.
- Allow authorized humans to mark `UNRESOLVED`, `EXPLAINED_NOT_CONFLICT`, or `RESOLVED_BY_HUMAN` according to schema.
- Require a rationale and chosen evidence/source for a human resolution.
- Persist an audit event.
- Do not delete contradictory evidence when a conflict is resolved; preserve history.

### Action proposals
- Create draft actions from supported requirements/impact paths only.
- Include owner/role proposal, deadline, rationale, evidence IDs, risk/priority and unresolved dependencies.
- Show a preview and require explicit approval.
- Use idempotency keys for approval writes.
- Only authorized users may approve/reject/resolve.
- No external emails, API side effects, or arbitrary tool execution in the MVP.

### Exit criteria
Human decision is recorded with actor, time and reason; rejected actions are not executed; duplicate approval retries don't duplicate data; invalid state transitions return clean errors; audit events can be retrieved by an authorized admin.

## 10. Milestone 8 — Frontend UX

Build pages in this priority order:
1. Dashboard shell and workspace context.
2. Document Explorer / upload status.
3. Document Detail + page evidence viewer.
4. Investigate workspace.
5. Decision Packet view.
6. Changes & Conflicts Center.
7. Impact Graph view (simple custom SVG/React view is sufficient; avoid a heavy graph library unless needed).
8. Action Review.
9. Evaluation/Trace Console (admin-only and redacted).

Reusable components:
- `AppShell`, `PageHeader`, `StatusBadge`, `EmptyState`, `ErrorState`, `LoadingState`.
- `DocumentUploadDropzone`, `ProcessingTimeline`, `DocumentTable`.
- `SourceCitation`, `EvidenceCard`, `SourcePagePreview`, `EvidenceDrawer`.
- `FactValue`, `DiffRow`, `ConflictComparison`.
- `ImpactPath`, `ImpactGraphCanvas`.
- `DecisionPacketPanel`, `ProposedActionCard`, `ApprovalDialog`.
- `MetricCard`, `TraceLink`, `UsageBudgetIndicator`.

Every page must have loading, empty, error and partial-success states. Avoid fake dashboard counters; any metric shown must be derived from database records or explicitly labelled demo data.

### Exit criteria
A judge can complete the principal scenario entirely through the deployed UI; clicking a citation opens the correct source page; conflict status and action approvals update from the backend; no fake interactions remain on the central demo path.

## 11. Milestone 9 — Logging, tracing, metrics, guardrails

### Structured logs
Emit JSON logs with timestamp, severity, event, request_id, trace_id, route, latency_ms, status_code, workspace hash/ID where safe, document/analysis IDs, stage, error code and usage metadata. Do not log API keys, access tokens, full PDFs, or sensitive raw prompts.

### Tracing
- Google Cloud Logging/Monitoring for service runtime, deployment, CPU/memory, request latency and error rates.
- Langfuse for trace/span hierarchy, model/prompt version, tool calls, latency and token usage, with redaction/minimization before export.
- Keep the app’s own `model_usage` table as a cost guard, not as the billing source of truth.

### Guardrails
- Upload validation and limits.
- Workspace/document authorization at every read/write and before retrieval.
- Prompt-injection separation: content from documents is untrusted data.
- Allowlisted tools only; bounded tool steps and API calls.
- Output schema validation.
- Citation IDs must be a subset of authorized retrieved evidence IDs.
- Evidence quote/page must map to a real stored chunk.
- Explicit abstention when evidence is insufficient.
- Timeout, limited retries, circuit breaker or disable switch for provider failures if feasible.
- Rate limits, daily model call cap, request-size and page limits.
- Human approval for consequential state changes.
- Audit log for conflicts resolved and actions approved/rejected.

### Exit criteria
For a given request ID, the developer can find a structured server log, corresponding Langfuse trace (when enabled), model usage record and evaluation result. Sensitive content is redacted or minimized.

## 12. Milestone 10 — Evaluation and regression suite

Create versioned JSONL/JSON dataset and expected outputs. Start with 30–50 cases using public/synthetic material:
- factual question;
- multi-document question;
- known date/amount conflict;
- policy added/removed/modified obligation;
- action extraction;
- unanswerable/abstention;
- prompt injection inside document;
- OCR/partial extraction;
- multilingual example where supported;
- no valid graph impact path;
- irrelevant or contradictory retrieved chunks.

Metrics:
- Recall@5 and MRR;
- citation validity and citation support;
- answer correctness/faithfulness;
- change-detection precision/recall/F1;
- conflict detection precision/recall/F1;
- graph edge precision/recall;
- impact path evidence coverage;
- schema-valid rate;
- abstention quality;
- latency, provider errors and token/cost estimates.

Build deterministic evaluator checks first. Use an LLM judge only for a limited, explicitly recorded sample; keep human review for high-impact claims. Report dataset size and actual metrics; never put invented numbers into UI, README, resume, or submission.

### Release gates
- All critical unit/security/API tests pass.
- Citation references are valid.
- Cross-workspace read/write tests pass.
- Known seeded conflict/change cases pass.
- At least one unanswerable case correctly abstains.
- No P0 prompt-injection test is known to cause unauthorized tool use or cross-tenant retrieval.
- Cost controls and provider usage checks are demonstrated.
- End-to-end demo works on the deployed environment.

## 13. CI/CD and delivery

### PR pipeline
1. Checkout source.
2. Set up pinned Python and Node versions.
3. Install backend dependencies from lock/pin configuration.
4. Run Ruff and Python type checks if configured.
5. Run pytest with model/provider calls mocked.
6. Run frontend lint/type checks/build.
7. Run dependency/security and secret scans.
8. Validate migrations against a disposable DB where feasible.
9. Build Docker image.
10. Deploy only from trusted main/release workflow after checks pass.
11. Run post-deploy `/health/live`, `/health/ready`, and smoke test.

Do not store cloud service account JSON or API tokens in repository files. Use GitHub secrets/OIDC where supported and least privilege.

### Deployment
- Frontend: Vercel within plan terms.
- API: GCP Cloud Run, request-based, min instances 0, conservative maximum instances (start at 1), bounded CPU/memory/concurrency/timeouts.
- Database/vector: Supabase PostgreSQL + pgvector.
- Files: Supabase private Storage.
- AI: Meta Model API.
- AI traces: Langfuse Cloud Hobby with redaction.
- Runtime telemetry: Cloud Logging/Monitoring.

Before deployment, verify region, quotas, database pooler/TLS behavior, secrets, CORS origins, trusted host, allowed MIME types, object bucket privacy and usage costs. Free quotas are not hard caps; keep cloud billing settings conservative and do not deploy if zero financial exposure is mandatory but not guaranteed.

## 14. Date-aware delivery plan (target: 4 November submission)

This schedule is aggressive. If a milestone slips, cut optional scope—not tests, provenance, or a working demo.

### 29 October — Foundation
- Validate repo and starter; run health, database, test, lint, frontend, and one Meta smoke call.
- Commit platform foundation and CI.

### 30 October — Schema and ingestion
- Alembic; document/page/chunk models; private storage adapter; upload validation; PDF extraction/OCR fallback.
- Demo at least one text PDF and one scanned/public synthetic sample.

### 31 October — Retrieval and fact extraction
- pgvector plus FTS; labelled retrieval cases; structured facts and evidence links.
- Verify cross-document selection and page citations.

### 1 November — Evidence-to-Impact Graph and conflict engine
- Nodes/edges; idempotent graph builder; requirement-level diffs; deterministic conflict candidates; bounded impact traversal.
- Seed one known contradiction and one impact path.

### 2 November — Decision Packet and approval UI
- Investigation route; Muse Spark structured result; citation validation; conflict review; one human-approved task flow.
- Make the main demo navigable without Swagger.

### 3 November — Evaluation, monitoring, deployment
- Run labelled dataset; correct top failures; check logs/traces, token budgets, security tests and deployed path; record demo video.

### 4 November — Freeze and submit early
- No feature expansion. Fix critical defects, verify public repo and demo URL, ensure hackathon-built changes are identifiable, publish only measured claims, and submit via official platform before deadline.

## 15. Scope cut policy

If behind schedule, cut in this order:
1. Optional Muse Image or any extra Meta model.
2. Fancy animated graph visualizations; use a static/interactive graph view with nodes and edges.
3. Advanced reranker.
4. Multi-user invitation/admin enhancements beyond demo-safe authorization.
5. More languages/doc types beyond tested examples.

Do **not** cut:
- source/page provenance;
- graph relationship provenance;
- conflict evidence on both sides;
- human approval for actions;
- workspace authorization;
- tests for the main flow;
- one live Meta model use in the product;
- one deployed end-to-end demonstration;
- truthful evaluation results.

## 16. Definition of done for a feature

A feature is not complete merely because the UI renders. It is done only when:
- API/schema contract is implemented;
- authorization is enforced;
- persisted state is consistent and idempotent;
- error/loading/empty states work;
- structured logs and relevant spans exist;
- unit/integration tests pass;
- edge cases in the PRD are handled or explicitly deferred;
- no new secrets or unlicensed assets were added;
- docs and README describe actual behavior, not intended future behavior.

## 17. End-to-end demo acceptance scenario

Using only public/synthetic documents with known expected facts:
1. Upload policy version A, policy version B, and a circular.
2. Show extracted facts with source pages.
3. Compare versions and show a labelled changed requirement.
4. Detect a labelled deadline/amount contradiction, show evidence on both sides.
5. Traverse a known relation to one potentially affected requirement/action.
6. Produce a Decision Packet with claims, evidence, uncertainties, and a proposed action.
7. Open original source passage from a citation.
8. Approve or reject an action as an authorized user; show the audit event.
9. Show the evaluation results and AI trace for that investigation.

## 18. Submission compliance checklist

- Public repository is reachable without login.
- README states the problem, why Meta models are used, architecture, setup, limitations, and actual measurements.
- New hackathon contributions are clearly identified by commits and a dedicated README section.
- All code, datasets, images, fonts and assets have usage rights.
- Demo video is three minutes, publicly accessible to judges, and shows only completed project features.
- Live demo URL works or there is a backup recording.
- No false accuracy, customer, deployment, or cost claims.
- Final submission completed through the official hackathon platform before deadline.
