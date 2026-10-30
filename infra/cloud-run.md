# Cloud Run deployment notes (do not deploy until the local vertical slice works)

1. Build the backend image from `backend/Dockerfile` and push it to Artifact Registry.
2. Deploy to Cloud Run in a region chosen after checking the current free-tier region eligibility and data location needs.
3. Set minimum instances to 0 and maximum instances to 1 for the initial demo; start with 1 CPU and 1 GiB, then benchmark memory before changing it.
4. Set production `APP_ENV=production`, exact `ALLOWED_ORIGINS`, `DATABASE_URL`, `MODEL_API_KEY`, and optional Langfuse secrets using Secret Manager/environment secret references. Never bake secrets into the image.
5. Use a Supabase PostgreSQL connection method intended for server-side runtime and configure TLS. Validate connection pool size against Cloud Run maximum instances; avoid large per-instance pools.
6. Disable dev-only `/internal/dev/*` routes automatically by setting `APP_ENV=production`.
7. Confirm Cloud Run `/health/live` and `/health/ready` before pointing Vercel at the API.
8. Configure the frontend's `NEXT_PUBLIC_API_BASE_URL` to the Cloud Run URL, then set CORS to that exact frontend origin.
9. Configure budget alerts and inspect actual billed usage after testing. A budget alert is a notification—not a hard cost cap.
10. Do not add Cloud SQL, Memorystore, GPUs, Kubernetes, or extra paid services for this MVP.

Free-tier note: Cloud Run, Artifact Registry, Cloud Build, network transfer, logging, and managed secrets have separate conditions/quotas. Verify current official pricing for the chosen region and account before deploying. No general free-tier plan can guarantee $0 if a billing account is enabled and a usage limit is exceeded.
