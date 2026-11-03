import { describeError, isRecord, requestJson } from "./client";
import type {
  ExtractedFact,
  ExtractionRun,
  ExtractResult,
  FactFilters,
  ListResult,
  Paginated,
  Requirement,
  RequirementFilters,
} from "./types";

function docPath(workspaceId: string, documentId: string): string {
  return `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/documents/${encodeURIComponent(documentId)}`;
}

/** A run summary is recognised by shape, never by HTTP status (409/503 can be either kind). */
export function isExtractionRun(body: unknown): body is ExtractionRun {
  return (
    isRecord(body) &&
    typeof body.extraction_run_id === "string" &&
    Array.isArray(body.pages) &&
    typeof body.status === "string" &&
    typeof body.document_id === "string"
  );
}

/**
 * POST /extract. No body. Sent exactly once; never retried here.
 * A failed run (non-200) that carries a full summary is returned as kind "run".
 */
export async function extractDocument(workspaceId: string, documentId: string): Promise<ExtractResult> {
  const { status, body } = await requestJson(`${docPath(workspaceId, documentId)}/extract`, { method: "POST" });
  if (isExtractionRun(body)) {
    return { kind: "run", httpStatus: status ?? 200, run: body };
  }
  if (status !== null && status >= 200 && status < 300) {
    return { kind: "error", httpStatus: status, message: "The server returned an unexpected response." };
  }
  return { kind: "error", httpStatus: status, message: describeError(status, body) };
}

function isPaginated<T>(body: unknown): body is Paginated<T> {
  return (
    isRecord(body) &&
    Array.isArray(body.items) &&
    typeof body.total === "number" &&
    typeof body.limit === "number" &&
    typeof body.offset === "number"
  );
}

async function listPaginated<T>(path: string, params: Record<string, string | number | undefined>): Promise<ListResult<T>> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  const qs = query.toString();
  const { status, body } = await requestJson(qs ? `${path}?${qs}` : path);
  if (status !== null && status >= 200 && status < 300 && isPaginated<T>(body)) {
    return { ok: true, data: body };
  }
  if (status !== null && status >= 200 && status < 300) {
    return { ok: false, status, message: "The server returned an unexpected response." };
  }
  return { ok: false, status, message: describeError(status, body) };
}

export function listFacts(
  workspaceId: string,
  documentId: string,
  filters: FactFilters = {},
): Promise<ListResult<ExtractedFact>> {
  return listPaginated<ExtractedFact>(`${docPath(workspaceId, documentId)}/facts`, {
    limit: filters.limit,
    offset: filters.offset,
    fact_type: filters.fact_type,
    verification_status: filters.verification_status,
  });
}

export function listRequirements(
  workspaceId: string,
  documentId: string,
  filters: RequirementFilters = {},
): Promise<ListResult<Requirement>> {
  return listPaginated<Requirement>(`${docPath(workspaceId, documentId)}/requirements`, {
    limit: filters.limit,
    offset: filters.offset,
    status: filters.status,
  });
}
