"use client";

import { useEffect, useState } from "react";
import { getOverview, getWorkspaces, type WorkspaceMembership, type WorkspaceOverview } from "@/lib/api/workspaces";

export function WorkspaceDashboard() {
  const [workspaces, setWorkspaces] = useState<WorkspaceMembership[]>([]);
  const [selected, setSelected] = useState("");
  const [overview, setOverview] = useState<WorkspaceOverview | null>(null);
  const [message, setMessage] = useState("Loading your workspaces…");
  useEffect(() => { void getWorkspaces().then((result) => { if (!result.ok) return setMessage(result.message); setWorkspaces(result.data); if (!result.data.length) return setMessage("You do not have access to a workspace yet. Ask a workspace administrator to add you."); setSelected(result.data[0].workspace_id); setMessage(""); }); }, []);
  useEffect(() => { if (!selected) return; setOverview(null); void getOverview(selected).then((result) => { if (result.ok) setOverview(result.data); else setMessage(result.message); }); }, [selected]);
  if (!workspaces.length) return <section className="product-empty"><div className="empty-icon">○</div><h2>No workspace access</h2><p>{message}</p></section>;
  return <><label className="workspace-select">Workspace<select value={selected} onChange={(event) => setSelected(event.target.value)}>{workspaces.map((workspace) => <option key={workspace.workspace_id} value={workspace.workspace_id}>{workspace.name} · {workspace.role}</option>)}</select></label>{!overview ? <p className="x-loading"><span className="x-spinner" /> Loading live overview…</p> : <section className="stats-grid">{[["Documents", overview.total_documents], ["Processing", overview.processing_documents], ["Ready", overview.ready_documents], ["Failed", overview.failed_documents], ["Extracted facts", overview.extracted_facts], ["Requirements", overview.requirements], ["Pending human reviews", overview.pending_reviews]].map(([label, value]) => <article className="stat-card" key={String(label)}><span>{label}</span><strong>{value}</strong><small>Workspace-authorized data</small></article>)}</section>}</>;
}
