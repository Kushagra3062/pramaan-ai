/**
 * TypeScript mirrors of the backend extraction API schemas
 * (backend/app/schemas/extraction_api.py). Nullability matches the Pydantic models.
 * Reason/status unions accept unknown future strings so the UI never crashes on them.
 */

// `string & Record<never, never>` keeps literal autocompletion while allowing unknown strings.
type OpenString<T extends string> = T | (string & Record<never, never>);

export type RunStatus = OpenString<"COMPLETED" | "PARTIAL" | "FAILED">;
export type PageStatus = OpenString<"PROCESSED" | "SKIPPED" | "FAILED" | "NOT_PROCESSED">;
export type PageReason = OpenString<
  | "PAGE_NOT_COMPLETED"
  | "EMPTY_CANONICAL_TEXT"
  | "NO_CANONICAL_CHUNKS"
  | "RATE_LIMITED"
  | "TIMEOUT"
  | "CONNECTION_ERROR"
  | "API_STATUS_ERROR"
  | "API_ERROR"
  | "EMPTY_RESPONSE"
  | "INVALID_JSON"
  | "SCHEMA_VALIDATION_FAILED"
  | "RESPONSE_TRUNCATED"
  | "MODEL_REFUSAL"
  | "UNEXPECTED_TOOL_CALLS"
  | "UNEXPECTED_FINISH_REASON"
  | "EXTRACTOR_UNAVAILABLE"
  | "RUN_STOPPED_AFTER_SYSTEMIC_ERROR"
  | "PAGE_STATE_CHANGED"
>;

export type FactType =
  | "DATE"
  | "AMOUNT"
  | "CURRENCY"
  | "OBLIGATION"
  | "DEADLINE"
  | "APPROVAL_CONDITION"
  | "IDENTIFIER"
  | "RESPONSIBLE_DEPARTMENT";
export const FACT_TYPES: FactType[] = [
  "DATE",
  "AMOUNT",
  "CURRENCY",
  "OBLIGATION",
  "DEADLINE",
  "APPROVAL_CONDITION",
  "IDENTIFIER",
  "RESPONSIBLE_DEPARTMENT",
];

export type VerificationStatus = "UNVERIFIED" | "VERIFIED" | "REJECTED";
export const VERIFICATION_STATUSES: VerificationStatus[] = ["UNVERIFIED", "VERIFIED", "REJECTED"];

export type RequirementStatus = "ACTIVE" | "SUPERSEDED" | "INACTIVE" | "DRAFT";
export const REQUIREMENT_STATUSES: RequirementStatus[] = ["DRAFT", "ACTIVE", "SUPERSEDED", "INACTIVE"];

export interface EvidenceReference {
  evidence_link_id: string;
  chunk_id: string;
  chunk_index: number;
  page_number: number;
  relationship_type: string;
  verification_status: string;
}

export interface Provenance {
  extraction_run_id: string | null;
  model_id: string | null;
  prompt_version: string | null;
  extraction_method: string | null;
  extracted_at: string | null;
  page_number: number | null;
  review_status: string | null;
}

export interface ExtractedFact {
  fact_id: string;
  fact_type: string;
  raw_value: string;
  normalized_value: Record<string, unknown> | null;
  source_quote: string;
  page_number: number | null;
  extraction_method: string;
  verification_status: string;
  provenance: Provenance | null;
  evidence: EvidenceReference[];
  created_at: string;
  updated_at: string;
}

export interface Requirement {
  requirement_id: string;
  title: string;
  description: string;
  requirement_type: string;
  normalized_value: Record<string, unknown> | null;
  status: string;
  review_status: string | null;
  source_quote: string | null;
  provenance: Provenance | null;
  evidence: EvidenceReference[];
  created_at: string;
  updated_at: string;
}

export interface Paginated<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface PageExtractionResult {
  page_number: number;
  status: PageStatus;
  reason: PageReason | null;
  page_extraction_status: string | null;
  chunks_processed: number;
  requests_succeeded: number;
  requests_failed: number;
  facts_created: number;
  facts_reused: number;
  requirements_created: number;
  requirements_reused: number;
  evidence_links_created: number;
  proposals_skipped: number;
  skip_reasons: Record<string, number>;
}

export interface ExtractionRun {
  workspace_id: string;
  document_id: string;
  extraction_run_id: string;
  model_id: string;
  prompt_version: string;
  started_at: string;
  completed_at: string;
  duration_ms: number;
  status: RunStatus;
  page_count: number;
  pages_processed: number;
  pages_skipped: number;
  pages_failed: number;
  pages_not_processed: number;
  chunk_requests: number;
  model_requests_succeeded: number;
  model_requests_failed: number;
  facts_created: number;
  facts_reused: number;
  requirements_created: number;
  requirements_reused: number;
  evidence_links_created: number;
  proposals_skipped: number;
  pages: PageExtractionResult[];
}

/** Outcome of POST /extract: a typed run summary (any HTTP status) or a plain error. */
export type ExtractResult =
  | { kind: "run"; httpStatus: number; run: ExtractionRun }
  | { kind: "error"; httpStatus: number | null; message: string };

export type ListResult<T> =
  | { ok: true; data: Paginated<T> }
  | { ok: false; status: number | null; message: string };

export interface FactFilters {
  limit?: number;
  offset?: number;
  fact_type?: FactType | "";
  verification_status?: VerificationStatus | "";
}

export interface RequirementFilters {
  limit?: number;
  offset?: number;
  status?: RequirementStatus | "";
}
