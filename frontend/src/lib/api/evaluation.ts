import { describeError, isRecord, requestJson } from "./client";

export interface EvalDataset { dataset_version: string; evaluator_version: string; case_count: number; case_ids: string[]; languages: string[]; }
export interface EvalRunReport { run_id: string; run_status: string; mode: string; self_check: boolean; model_calls: number; scores_pushed_to_langfuse: boolean; dataset_version: string; evaluator_version: string; facts: { precision: number | null; recall: number | null; f1: number | null }; requirements: { precision: number | null; recall: number | null; f1: number | null }; evidence_grounding: Record<string, number | null>; multilingual_integrity: Record<string, number | null>; quality: Record<string, number | null>; per_case: { case_id: string; language: string; facts: { f1: number | null }; requirements: { f1: number | null }; missing_prediction: boolean }[]; [key: string]: unknown; }
export interface EvalRunStart { run_id: string; run_status: string; mode: string; dataset_version: string; evaluator_version: string; report: EvalRunReport; }
export interface EvalRunSummary { id: string; dataset_version: string; evaluator_version: string; status: string; metrics: Record<string, unknown> | null; created_at: string; completed_at: string | null; }
export interface EvalCase { case_id: string; status: string; scores: Record<string, unknown> | null; expected_output: Record<string, unknown> | null; actual_output: Record<string, unknown> | null; }
export interface EvalRunDetail extends EvalRunSummary { cases: EvalCase[]; }
type Result<T> = { ok: true; data: T } | { ok: false; message: string; status: number | null };
function base(workspaceId: string) { return `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/evaluation`; }
export async function listDatasets(workspaceId: string): Promise<Result<EvalDataset[]>> {
  const response = await requestJson(`${base(workspaceId)}/datasets`);
  return response.status !== null && response.status < 300 && Array.isArray(response.body)
    ? { ok: true, data: response.body as EvalDataset[] }
    : { ok: false, status: response.status, message: describeError(response.status, response.body) };
}
export async function startEvalRun(workspaceId: string, mode: "mocked" | "live", pushScores: boolean): Promise<Result<EvalRunStart>> {
  const response = await requestJson(`${base(workspaceId)}/runs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode, push_langfuse_scores: pushScores }) });
  return response.status !== null && response.status < 300 && isRecord(response.body) && typeof response.body.run_id === "string"
    ? { ok: true, data: response.body as unknown as EvalRunStart }
    : { ok: false, status: response.status, message: describeError(response.status, response.body) };
}
export async function listEvalRuns(workspaceId: string): Promise<Result<{ items: EvalRunSummary[]; total: number }>> {
  const response = await requestJson(`${base(workspaceId)}/runs?${new URLSearchParams({ limit: "20" })}`);
  return response.status !== null && response.status < 300 && isRecord(response.body) && Array.isArray(response.body.items)
    ? { ok: true, data: { items: response.body.items as EvalRunSummary[], total: Number(response.body.total || 0) } }
    : { ok: false, status: response.status, message: describeError(response.status, response.body) };
}
export async function getEvalRun(workspaceId: string, runId: string): Promise<Result<EvalRunDetail>> {
  const response = await requestJson(`${base(workspaceId)}/runs/${encodeURIComponent(runId)}`);
  return response.status !== null && response.status < 300 && isRecord(response.body) && Array.isArray(response.body.cases)
    ? { ok: true, data: response.body as unknown as EvalRunDetail }
    : { ok: false, status: response.status, message: describeError(response.status, response.body) };
}
