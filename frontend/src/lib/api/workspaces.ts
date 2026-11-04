import { describeError, isRecord, requestJson } from "./client";

export interface WorkspaceMembership { workspace_id: string; name: string; slug: string; role: string; }
export interface WorkspaceOverview { workspace_id: string; total_documents: number; processing_documents: number; ready_documents: number; failed_documents: number; extracted_facts: number; requirements: number; pending_reviews: number; }

function memberships(value: unknown): value is WorkspaceMembership[] {
  return Array.isArray(value) && value.every((item) => isRecord(item) && typeof item.workspace_id === "string" && typeof item.name === "string" && typeof item.role === "string");
}
export async function getWorkspaces(): Promise<{ ok: true; data: WorkspaceMembership[] } | { ok: false; message: string }> {
  const { status, body } = await requestJson("/api/v1/workspaces");
  return status !== null && status >= 200 && status < 300 && memberships(body) ? { ok: true, data: body } : { ok: false, message: describeError(status, body) };
}
export async function getOverview(id: string): Promise<{ ok: true; data: WorkspaceOverview } | { ok: false; message: string }> {
  const { status, body } = await requestJson(`/api/v1/workspaces/${encodeURIComponent(id)}/overview`);
  return status !== null && status >= 200 && status < 300 && isRecord(body) ? { ok: true, data: body as unknown as WorkspaceOverview } : { ok: false, message: describeError(status, body) };
}

export interface ActivityEvent { id: string; event_type: string; entity_type: string; entity_id: string | null; actor_user_id: string | null; request_id: string | null; created_at: string; }
export interface UsageItem { id: string; provider: string; model_id: string; request_id: string | null; input_tokens: number; output_tokens: number; latency_ms: number; estimated_cost: number | null; status: string; analysis_id: string | null; created_at: string; }
export interface UsageOverview { total_input_tokens: number; total_output_tokens: number; total_requests: number; items: UsageItem[]; }
export interface CurrentUser { user_id: string; email: string; display_name: string; }

export async function getActivity(id: string): Promise<{ ok: true; data: ActivityEvent[] } | { ok: false; message: string }> {
  const { status, body } = await requestJson(`/api/v1/workspaces/${encodeURIComponent(id)}/activity`);
  return status !== null && status >= 200 && status < 300 && Array.isArray(body) ? { ok: true, data: body as ActivityEvent[] } : { ok: false, message: describeError(status, body) };
}
export async function getUsage(id: string): Promise<{ ok: true; data: UsageOverview } | { ok: false; message: string }> {
  const { status, body } = await requestJson(`/api/v1/workspaces/${encodeURIComponent(id)}/usage`);
  return status !== null && status >= 200 && status < 300 && isRecord(body) ? { ok: true, data: body as unknown as UsageOverview } : { ok: false, message: describeError(status, body) };
}
export async function getCurrentUser(): Promise<{ ok: true; data: CurrentUser } | { ok: false; message: string }> {
  const { status, body } = await requestJson("/api/v1/me");
  return status !== null && status >= 200 && status < 300 && isRecord(body) ? { ok: true, data: body as unknown as CurrentUser } : { ok: false, message: describeError(status, body) };
}
