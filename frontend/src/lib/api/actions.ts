import { describeError, isRecord, requestJson } from "./client";

export interface ActionItem { id: string; workspace_id: string; description: string; priority: string; status: string; proposed_owner: string | null; due_date: string | null; related_analysis_id: string | null; related_conflict_id: string | null; idempotency_key: string | null; decision_rationale: string | null; approved_at: string | null; rejected_at: string | null; created_at: string; }
export interface ActionCreate { description: string; priority: string; proposed_owner?: string; due_date?: string; related_analysis_id?: string; related_conflict_id?: string; idempotency_key: string; }
type Result<T> = { ok: true; data: T } | { ok: false; message: string; status: number | null };
function base(workspaceId: string) { return `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/actions`; }
function isAction(value: unknown): value is ActionItem {
  return isRecord(value) && typeof value.id === "string" && typeof value.description === "string" && typeof value.status === "string";
}
export async function listActions(workspaceId: string, status = "", limit = 20, offset = 0): Promise<Result<{ items: ActionItem[]; total: number }>> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (status) query.set("status", status);
  const response = await requestJson(`${base(workspaceId)}?${query}`);
  return response.status !== null && response.status < 300 && isRecord(response.body) && Array.isArray(response.body.items)
    ? { ok: true, data: { items: (response.body.items as unknown[]).filter(isAction), total: Number(response.body.total || 0) } }
    : { ok: false, status: response.status, message: describeError(response.status, response.body) };
}
export async function proposeAction(workspaceId: string, body: ActionCreate): Promise<Result<ActionItem>> {
  const response = await requestJson(base(workspaceId), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return response.status !== null && response.status < 300 && isAction(response.body)
    ? { ok: true, data: response.body }
    : { ok: false, status: response.status, message: describeError(response.status, response.body) };
}
async function decide(workspaceId: string, id: string, action: "approve" | "reject", rationale: string): Promise<Result<ActionItem>> {
  const response = await requestJson(`${base(workspaceId)}/${encodeURIComponent(id)}/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rationale: rationale || null }) });
  return response.status !== null && response.status < 300 && isAction(response.body)
    ? { ok: true, data: response.body }
    : { ok: false, status: response.status, message: describeError(response.status, response.body) };
}
export const approveAction = (workspaceId: string, id: string, rationale: string) => decide(workspaceId, id, "approve", rationale);
export const rejectAction = (workspaceId: string, id: string, rationale: string) => decide(workspaceId, id, "reject", rationale);
