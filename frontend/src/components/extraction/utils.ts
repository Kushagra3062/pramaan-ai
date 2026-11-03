import type { PageReason } from "@/lib/api/types";

const DEBUG_MARKER = /^\[(?:OCR EXTRACTED|MUSE SPARK VISION|OCR|MUSE SPARK)\]:?\s*/gim;

/** Mirrors the backend's clean_canonical_text so eligibility matches what was chunked. */
export function cleanCanonicalText(text: string | null | undefined): string {
  if (!text) return "";
  return text.replace(DEBUG_MARKER, "").replace(/\n{3,}/g, "\n\n").trim();
}

export interface PageLike {
  page_number: number;
  extracted_text: string | null;
  extraction_status: string;
}

export interface ChunkLike {
  id: string;
  chunk_index: number;
  page_start: number;
  page_end: number;
  content: string;
}

/**
 * Eligible canonical chunks: single-page chunks of COMPLETED pages with non-empty cleaned text.
 * The backend remains authoritative (including its model-call limit); this is an estimate.
 */
export function countEligibleChunks(pages: PageLike[], chunks: ChunkLike[]): number {
  const eligiblePages = new Set(
    pages
      .filter((p) => p.extraction_status === "COMPLETED" && cleanCanonicalText(p.extracted_text) !== "")
      .map((p) => p.page_number),
  );
  return chunks.filter(
    (c) => c.page_start === c.page_end && eligiblePages.has(c.page_start) && c.content.trim() !== "",
  ).length;
}

const MALAYALAM = /[ഀ-ൿ]/;

/** `lang` attribute for a text value: "ml" when it contains Malayalam script. */
export function detectLang(text: string | null | undefined, hint?: string | null): string | undefined {
  if (hint && /^(ml|malayalam)/i.test(hint.trim())) return "ml";
  return text && MALAYALAM.test(text) ? "ml" : undefined;
}

export interface QuoteLocation {
  before: string;
  match: string;
  after: string;
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Locate `quote` inside `content` for highlighting, only when the match is unambiguous:
 * both strings must already be NFC, the quote is matched whitespace-tolerantly and
 * case-sensitively, and there must be exactly one match. Otherwise returns null and the
 * caller shows the text without highlighting. Text is never altered.
 */
export function locateQuote(content: string, quote: string | null | undefined): QuoteLocation | null {
  if (!quote) return null;
  if (content.normalize("NFC") !== content || quote.normalize("NFC") !== quote) return null;
  const tokens = quote.trim().split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return null;
  const pattern = new RegExp(tokens.map(escapeRegExp).join("\\s+"), "g");
  const first = pattern.exec(content);
  if (!first || first[0].length === 0) return null;
  if (pattern.exec(content) !== null) return null; // ambiguous: more than one match
  const start = first.index;
  const end = start + first[0].length;
  return { before: content.slice(0, start), match: first[0], after: content.slice(end) };
}

/** Hides backend-internal keys from any model-controlled metadata object. */
export function visibleMetadata(value: Record<string, unknown> | null | undefined): [string, unknown][] {
  if (!value) return [];
  return Object.entries(value).filter(
    ([key, v]) => !key.startsWith("_") && key !== "proposal_fingerprint" && v !== null && v !== undefined && v !== "",
  );
}

export function asText(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

export function formatDuration(ms: number | null | undefined): string {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

export function shortId(id: string | null | undefined): string {
  return id ? id.slice(0, 8) : "—";
}

export function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

const REASON_TEXT: Record<string, string> = {
  PAGE_NOT_COMPLETED: "Canonical extraction for this page is not completed",
  EMPTY_CANONICAL_TEXT: "No canonical text on this page",
  NO_CANONICAL_CHUNKS: "No canonical chunks for this page",
  RATE_LIMITED: "Model rate limit reached",
  TIMEOUT: "Model request timed out",
  CONNECTION_ERROR: "Could not connect to the model service",
  API_STATUS_ERROR: "Model service returned an error",
  API_ERROR: "Model service request failed",
  EMPTY_RESPONSE: "Model returned an empty response",
  INVALID_JSON: "Model response was not valid JSON",
  SCHEMA_VALIDATION_FAILED: "Model response did not match the expected structure",
  RESPONSE_TRUNCATED: "Model output was cut off at its token limit, so the response is incomplete and was discarded",
  MODEL_REFUSAL: "Model refused or its output was blocked, and returned no usable content",
  UNEXPECTED_TOOL_CALLS: "Model returned tool calls instead of the requested JSON",
  UNEXPECTED_FINISH_REASON: "Model response ended in an unexpected way and was discarded",
  EXTRACTOR_UNAVAILABLE: "Extraction service is unavailable",
  RUN_STOPPED_AFTER_SYSTEMIC_ERROR: "Not attempted: run stopped after a service-level failure",
  PAGE_STATE_CHANGED: "Page changed while the run was in progress",
};

const SKIP_TEXT: Record<string, string> = {
  INVALID_QUOTE: "invalid quote",
  QUOTE_NOT_IN_PAGE: "quote not found in page text",
  QUOTE_IN_PAGE_NO_CHUNK: "quote not inside a single chunk",
  AMBIGUOUS_CHUNK_MATCH: "quote matched several chunks",
  TITLE_TOO_LONG: "title too long",
  FACT_VALUE_NOT_IN_EVIDENCE: "fact value not in its quote",
};

export function reasonText(reason: PageReason | null | undefined): string {
  if (!reason) return "—";
  return REASON_TEXT[reason] ?? String(reason);
}

export function skipReasonText(code: string): string {
  return SKIP_TEXT[code] ?? code;
}
