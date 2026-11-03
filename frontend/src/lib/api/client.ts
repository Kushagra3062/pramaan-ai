/**
 * Minimal shared API client for the structured-extraction feature.
 * Native fetch only. Never surfaces raw payloads, stack traces or secrets to the UI.
 */

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export interface RawResponse {
  /** HTTP status, or null when the request never completed (network failure). */
  status: number | null;
  /** Parsed JSON body, or null when absent / not JSON. */
  body: unknown;
}

async function authHeaders(init?: RequestInit): Promise<Headers> {
  const headers = new Headers(init?.headers);
  const { getSupabaseBrowserClient } = await import("@/lib/supabase/browser");
  const client = getSupabaseBrowserClient();
  const { data } = client ? await client.auth.getSession() : { data: { session: null } };
  if (data.session?.access_token) headers.set("Authorization", `Bearer ${data.session.access_token}`);
  return headers;
}

/** One fetch, JSON parsed defensively. Never throws; never retries. */
export async function requestJson(path: string, init?: RequestInit): Promise<RawResponse> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { cache: "no-store", ...init, headers: await authHeaders(init) });
  } catch {
    return { status: null, body: null };
  }
  let body: unknown = null;
  try {
    const text = await res.text();
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  return { status: res.status, body };
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const UNSAFE_DETAIL = /traceback|\bselect\b[\s\S]*\bfrom\b|\binsert\b[\s\S]*\binto\b|api[_-]?key|bearer\s|\bsecret\b|\.py\b/i;

function safeString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const text = value.trim();
  if (!text || text.length > 300 || text.includes("\n") || UNSAFE_DETAIL.test(text)) return null;
  return text;
}

/** User-safe text extracted from a FastAPI error body (string detail or 422 array). */
export function extractSafeDetail(body: unknown): string | null {
  if (!isRecord(body)) return null;
  const detail = body.detail;
  const direct = safeString(detail);
  if (direct) return direct;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (!isRecord(item)) return null;
        const msg = safeString(item.msg);
        if (!msg) return null;
        const loc = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : null;
        return typeof loc === "string" || typeof loc === "number" ? `${loc}: ${msg}` : msg;
      })
      .filter((p): p is string => p !== null)
      .slice(0, 3);
    if (parts.length > 0) return `Invalid request — ${parts.join("; ")}`;
  }
  return null;
}

export function defaultMessageForStatus(status: number | null): string {
  switch (status) {
    case null:
      return "Could not reach the server. The request may still be running or may have completed on the server — refresh the lists before trying again.";
    case 404:
      return "Document not found.";
    case 409:
      return "Document is not ready or its source state changed.";
    case 413:
      return "This document contains more eligible chunks than the server allows in one extraction request.";
    case 422:
      return "The request was not valid.";
    case 429:
      return "Model rate limit reached.";
    case 500:
      return "Extraction was rolled back because the backend encountered an error.";
    case 502:
      return "Model extraction returned an unusable response or the service failed.";
    case 503:
      return "Structured extraction service is unavailable.";
    case 504:
      return "Model request timed out.";
    default:
      return `The server returned an unexpected response (HTTP ${status}).`;
  }
}

/** Friendly status-based message, plus the backend's own detail when it is safe and informative. */
export function describeError(status: number | null, body: unknown): string {
  const base = defaultMessageForStatus(status);
  const detail = extractSafeDetail(body);
  if (!detail || status === null) return base;
  // 413 details carry the counts ("requires N calls ... maximum of M"), so show them in full.
  return status === 413 ? `${base} ${detail}` : detail === base ? base : `${base} (${detail})`;
}
