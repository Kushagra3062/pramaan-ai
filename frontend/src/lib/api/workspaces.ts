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
