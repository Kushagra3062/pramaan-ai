import { describeError, isRecord, requestJson } from "./client";

export interface ConflictSide { fact_id: string; document_id: string; document_filename: string; fact_type: string; raw_value: string; source_quote: string; page_number: number | null; }
export interface ConflictItem { id: string; workspace_id: string; conflict_type: string; severity: string; status: string; explanation: string; resolution_note: string | null; resolved_at: string | null; created_at: string; fact_a: ConflictSide; fact_b: ConflictSide; }
export interface DetectionResult { facts_scanned: number; pairs_compared: number; candidates_created: number; candidates_skipped_existing: number; conflicts: ConflictItem[]; }
type Result<T> = { ok: true; data: T } | { ok: false; message: string; status: number | null };
function base(workspaceId: string) { return `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/conflicts`; }
function isConflict(value: unknown): value is ConflictItem {
  return isRecord(value) && typeof value.id === "string" && typeof value.status === "string" && isRecord(value.fact_a) && isRecord(value.fact_b);
}
export async function listConflicts(workspaceId: string, status = "", limit = 20, offset = 0): Promise<Result<{ items: ConflictItem[]; total: number }>> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (status) query.set("status", status);
  const response = await requestJson(`${base(workspaceId)}?${query}`);
  return response.status !== null && response.status < 300 && isRecord(response.body) && Array.isArray(response.body.items)
    ? { ok: true, data: { items: (response.body.items as unknown[]).filter(isConflict), total: Number(response.body.total || 0) } }
    : { ok: false, status: response.status, message: describeError(response.status, response.body) };
}
export async function detectConflicts(workspaceId: string): Promise<Result<DetectionResult>> {
  const response = await requestJson(`${base(workspaceId)}/detect`, { method: "POST" });
  return response.status !== null && response.status < 300 && isRecord(response.body) && Array.isArray(response.body.conflicts)
    ? { ok: true, data: response.body as unknown as DetectionResult }
    : { ok: false, status: response.status, message: describeError(response.status, response.body) };
}
async function transition(workspaceId: string, id: string, action: "resolve" | "dismiss", note: string): Promise<Result<ConflictItem>> {
  const response = await requestJson(`${base(workspaceId)}/${encodeURIComponent(id)}/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ note }) });
  return response.status !== null && response.status < 300 && isConflict(response.body)
    ? { ok: true, data: response.body }
    : { ok: false, status: response.status, message: describeError(response.status, response.body) };
}
export const resolveConflict = (workspaceId: string, id: string, note: string) => transition(workspaceId, id, "resolve", note);
export const dismissConflict = (workspaceId: string, id: string, note: string) => transition(workspaceId, id, "dismiss", note);
