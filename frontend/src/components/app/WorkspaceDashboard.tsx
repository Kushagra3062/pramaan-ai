"use client";

import { useEffect, useState } from "react";
import { getOverview, getWorkspaces, type WorkspaceMembership, type WorkspaceOverview } from "@/lib/api/workspaces";

export function WorkspaceDashboard() {
  const [workspaces, setWorkspaces] = useState<WorkspaceMembership[]>([]);
  const [selected, setSelected] = useState("");
  const [overview, setOverview] = useState<WorkspaceOverview | null>(null);
  const [message, setMessage] = useState("Loading your workspaces…");
  const [loading, setLoading] = useState(true);
  const [overviewError, setOverviewError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const retry = () => setAttempt((n) => n + 1);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setMessage("Loading your workspaces…");
    void getWorkspaces().then((result) => {
      if (cancelled) return;
      setLoading(false);
      if (!result.ok) return setMessage(result.message);
      setWorkspaces(result.data);
      if (!result.data.length) return setMessage("You do not have access to a workspace yet. Ask a workspace administrator to add you.");
      setSelected((prev) => prev || result.data[0].workspace_id);
      setMessage("");
    });
    return () => { cancelled = true; };
  }, [attempt]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setOverview(null);
    setOverviewError(null);
    void getOverview(selected).then((result) => {
      if (cancelled) return;
      if (result.ok) setOverview(result.data);
      else setOverviewError(result.message);
    });
    return () => { cancelled = true; };
  }, [selected, attempt]);

  if (!workspaces.length) {
    return (
      <section className="product-empty">
        <div className="empty-icon" aria-hidden="true">○</div>
        <h2>{loading ? "Loading workspaces" : "No workspace access"}</h2>
        <p>{message}</p>
        {!loading && <button type="button" className="secondary-button" onClick={retry}>Retry</button>}
      </section>
    );
  }

  const metrics = [["Documents", overview?.total_documents, "Across this workspace"], ["Processing", overview?.processing_documents, "Awaiting completion"], ["Ready", overview?.ready_documents, "Available to inspect"], ["Failed", overview?.failed_documents, "Needs attention"], ["Extracted facts", overview?.extracted_facts, "Source-linked proposals"], ["Requirements", overview?.requirements, "Awaiting review where applicable"], ["Pending human reviews", overview?.pending_reviews, "Review queue"]] as const;

  return (
    <section className="dashboard-content">
      <div className="workspace-bar">
        <label className="workspace-select">Active workspace<select value={selected} onChange={(event) => setSelected(event.target.value)}>{workspaces.map((workspace) => <option key={workspace.workspace_id} value={workspace.workspace_id}>{workspace.name} · {workspace.role}</option>)}</select></label>
        <p><span className="live-dot" /> Live, workspace-authorized data</p>
      </div>
      {!overview ? (
        overviewError ? (
          <div className="product-empty">
            <div className="empty-icon" aria-hidden="true">○</div>
            <h2>Could not load overview</h2>
            <p>{overviewError}</p>
            <button type="button" className="secondary-button" onClick={retry}>Retry</button>
          </div>
        ) : (
          <div className="dashboard-loading"><span className="x-spinner" /> Loading your live overview…</div>
        )
      ) : (
        <section className="stats-grid">{metrics.map(([label, value, detail]) => <article className="stat-card" key={label}><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>)}</section>
      )}
    </section>
  );
}
