import { describeError, isRecord, requestJson } from "./client";

export interface GraphNode { id: string; node_type: string; entity_key: string; display_label: string; metadata: Record<string, unknown>; created_at: string; }
export interface GraphEdge { id: string; source_node_id: string; target_node_id: string; relationship_type: string; provenance_type: string; verification_status: string; confidence: number | null; validated: boolean; metadata: Record<string, unknown>; }
export interface ImpactPath { node_ids: string[]; edge_ids: string[]; validated: boolean; }
export interface ImpactResult { start_node_id: string; max_depth: number; truncated: boolean; reached_nodes: number; nodes: GraphNode[]; edges: GraphEdge[]; validated_paths: ImpactPath[]; candidate_paths: ImpactPath[]; }
type Result<T> = { ok: true; data: T } | { ok: false; message: string; status: number | null };
function base(workspaceId: string) { return `/api/v1/workspaces/${encodeURIComponent(workspaceId)}/graph`; }
export async function listGraphNodes(workspaceId: string, limit = 50): Promise<Result<{ items: GraphNode[]; total: number }>> {
  const response = await requestJson(`${base(workspaceId)}/nodes?${new URLSearchParams({ limit: String(limit) })}`);
  return response.status !== null && response.status < 300 && isRecord(response.body) && Array.isArray(response.body.items)
    ? { ok: true, data: { items: response.body.items as GraphNode[], total: Number(response.body.total || 0) } }
    : { ok: false, status: response.status, message: describeError(response.status, response.body) };
}
export async function listGraphEdges(workspaceId: string, limit = 100): Promise<Result<{ items: GraphEdge[]; total: number }>> {
  const response = await requestJson(`${base(workspaceId)}/edges?${new URLSearchParams({ limit: String(limit) })}`);
  return response.status !== null && response.status < 300 && isRecord(response.body) && Array.isArray(response.body.items)
    ? { ok: true, data: { items: response.body.items as GraphEdge[], total: Number(response.body.total || 0) } }
    : { ok: false, status: response.status, message: describeError(response.status, response.body) };
}
export async function getImpact(workspaceId: string, nodeId: string, depth = 2): Promise<Result<ImpactResult>> {
  const query = new URLSearchParams({ node_id: nodeId, depth: String(depth) });
  const response = await requestJson(`${base(workspaceId)}/impact?${query}`);
  return response.status !== null && response.status < 300 && isRecord(response.body) && Array.isArray(response.body.nodes)
    ? { ok: true, data: response.body as unknown as ImpactResult }
    : { ok: false, status: response.status, message: describeError(response.status, response.body) };
}
