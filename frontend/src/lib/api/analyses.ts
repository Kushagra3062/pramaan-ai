import { describeError, isRecord, requestJson } from "./client";

export interface AnalysisSummary { id: string; workspace_id: string; question: string; status: string; model_id: string | null; prompt_version: string | null; created_at: string; completed_at: string | null; }
export interface ClaimEvidenceItem { chunk_id: string; document_id: string; document_filename: string; page_start: number; page_end: number; excerpt: string; evidence_role: string; }
export interface AnalysisClaimItem { id: string; claim_text: string; support_status: string; evidence: ClaimEvidenceItem[]; }
export interface AnalysisDetail extends AnalysisSummary { result: Record<string, unknown> | null; claims: AnalysisClaimItem[]; }
type Result<T> = { ok: true; data: T } | { ok: false; message: string; status: number | null };
function base(workspaceId: string) { return `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/analyses`; }
export async function listAnalyses(workspaceId: string, limit = 20, offset = 0): Promise<Result<{ items: AnalysisSummary[]; total: number }>> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  const response = await requestJson(`${base(workspaceId)}?${query}`);
  return response.status !== null && response.status < 300 && isRecord(response.body) && Array.isArray(response.body.items)
    ? { ok: true, data: { items: response.body.items as AnalysisSummary[], total: Number(response.body.total || 0) } }
    : { ok: false, status: response.status, message: describeError(response.status, response.body) };
}
export async function getAnalysis(workspaceId: string, id: string): Promise<Result<AnalysisDetail>> {
  const response = await requestJson(`${base(workspaceId)}/${encodeURIComponent(id)}`);
  return response.status !== null && response.status < 300 && isRecord(response.body) && typeof response.body.question === "string"
    ? { ok: true, data: response.body as unknown as AnalysisDetail }
    : { ok: false, status: response.status, message: describeError(response.status, response.body) };
}
export async function investigate(workspaceId: string, question: string, documentIds: string[]): Promise<Result<AnalysisDetail>> {
  const response = await requestJson(`${base(workspaceId)}/investigate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, document_ids: documentIds }) });
  return response.status !== null && response.status < 300 && isRecord(response.body) && typeof response.body.question === "string"
    ? { ok: true, data: response.body as unknown as AnalysisDetail }
    : { ok: false, status: response.status, message: describeError(response.status, response.body) };
}
