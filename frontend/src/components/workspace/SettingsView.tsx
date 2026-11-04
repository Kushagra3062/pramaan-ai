"use client";

import { useEffect, useState } from "react";
import { PageTitle } from "@/components/app/ProductPage";
import { EmptyCard, ErrorCard, LoadRow, StatusPill, formatDate } from "@/components/workspace/ui";
import { getActivity, getCurrentUser, getUsage, getWorkspaces, type ActivityEvent, type CurrentUser, type UsageOverview, type WorkspaceMembership } from "@/lib/api/workspaces";

export function SettingsView({ workspaceId }: Readonly<{ workspaceId: string }>) {
  const [membership, setMembership] = useState<WorkspaceMembership | null>(null);
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [usage, setUsage] = useState<UsageOverview | null>(null);
  const [activity, setActivity] = useState<ActivityEvent[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [members, me, use, act] = await Promise.all([
        getWorkspaces(), getCurrentUser(), getUsage(workspaceId), getActivity(workspaceId),
      ]);
      if (cancelled) return;
      setLoading(false);
      if (!members.ok) return setError(members.message);
      setMembership(members.data.find((item) => item.workspace_id === workspaceId) ?? null);
      if (me.ok) setUser(me.data);
      if (use.ok) setUsage(use.data);
      if (act.ok) setActivity(act.data);
      if (!use.ok && !act.ok) setError(use.message);
    })();
    return () => { cancelled = true; };
  }, [workspaceId]);

  if (loading) {
    return (
      <>
        <PageTitle eyebrow="Settings" title="Workspace settings" />
        <LoadRow text="Loading settings…" />
      </>
    );
  }
  if (error && !membership) {
    return (
      <>
        <PageTitle eyebrow="Settings" title="Workspace settings" />
        <ErrorCard title="Settings unavailable" message={error} />
      </>
    );
  }
  return (
    <>
      <PageTitle eyebrow="Settings" title="Workspace settings">
        <p>Identity, access model, usage ledger and observability disclosure for this workspace.</p>
      </PageTitle>
      <section className="x-card">
        <div className="x-card-head"><h3>Profile and workspace</h3></div>
        <dl className="x-meta">
          <div><dt>Signed in as</dt><dd>{user ? `${user.display_name} · ${user.email}` : "—"}</dd></div>
          <div><dt>Workspace</dt><dd>{membership?.name ?? "—"} ({membership?.slug ?? "—"})</dd></div>
          <div><dt>Your role</dt><dd>{membership?.role ?? "—"}</dd></div>
          <div><dt>Workspace ID</dt><dd><code>{workspaceId}</code></dd></div>
        </dl>
      </section>
      <section className="x-card">
        <div className="x-card-head"><h3>Access management</h3></div>
        <div className="x-table-wrap">
          <table className="x-table">
            <thead><tr><th>Role</th><th>Read</th><th>Upload / extract / investigate</th><th>Approve / resolve</th></tr></thead>
            <tbody>
              <tr><td>OWNER / ADMIN</td><td>yes</td><td>yes</td><td>yes</td></tr>
              <tr><td>EDITOR</td><td>yes</td><td>yes</td><td>yes</td></tr>
              <tr><td>REVIEWER / VIEWER</td><td>yes</td><td>no</td><td>no</td></tr>
            </tbody>
          </table>
        </div>
        <p className="x-note x-muted">Every read and write is checked server-side against membership. Membership changes are admin-managed in this release.</p>
      </section>
      <section className="x-card">
        <div className="x-card-head"><h3>Model and usage</h3><span className="x-muted x-note">{usage ? `${usage.total_requests} requests` : ""}</span></div>
        {!usage ? <EmptyCard title="No usage yet" detail="Model calls appear here with tokens, latency and outcome." /> : (
          <>
            <div className="x-grid">
              <div className="x-stat"><div className="x-stat-value">{usage.total_input_tokens.toLocaleString()}</div><div className="x-stat-label">Input tokens</div></div>
              <div className="x-stat"><div className="x-stat-value">{usage.total_output_tokens.toLocaleString()}</div><div className="x-stat-label">Output tokens</div></div>
              <div className="x-stat"><div className="x-stat-value">{usage.total_requests}</div><div className="x-stat-label">Requests</div></div>
              <div className="x-stat"><div className="x-stat-value">{usage.items.some((item) => item.estimated_cost !== null) ? "mixed" : "unknown"}</div><div className="x-stat-label">Cost (unknown, never zero)</div></div>
            </div>
            <div className="x-table-wrap">
              <table className="x-table">
                <thead><tr><th>When</th><th>Model</th><th>In / out</th><th>Latency</th><th>Status</th></tr></thead>
                <tbody>
                  {usage.items.slice(0, 25).map((item) => (
                    <tr key={item.id}>
                      <td>{formatDate(item.created_at)}</td>
                      <td><code>{item.model_id}</code></td>
                      <td>{item.input_tokens} / {item.output_tokens}</td>
                      <td>{Math.round(item.latency_ms)} ms</td>
                      <td><StatusPill value={item.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
      <section className="x-card">
        <div className="x-card-head"><h3>Observability and privacy</h3></div>
        <ul className="x-ul">
          <li>Structured logs carry request IDs across retrieval, model and validation stages.</li>
          <li>Langfuse traces receive identifiers, counts and outcomes only — never prompts, document text, quotes or responses.</li>
          <li>Evaluation scores are pushed only when explicitly requested per run.</li>
          <li>Log fields matching secret, token, key, prompt, response, quote or text patterns are redacted.</li>
        </ul>
      </section>
      <section className="x-card">
        <div className="x-card-head"><h3>Recent activity</h3><span className="x-muted x-note">{activity.length} events</span></div>
        {activity.length === 0 ? <EmptyCard title="No activity yet" detail="Uploads, extractions, resolutions and approvals are recorded here." /> : (
          <div className="x-table-wrap">
            <table className="x-table">
              <thead><tr><th>When</th><th>Event</th><th>Entity</th><th>Request</th></tr></thead>
              <tbody>
                {activity.slice(0, 25).map((event) => (
                  <tr key={event.id}>
                    <td>{formatDate(event.created_at)}</td>
                    <td><code>{event.event_type}</code></td>
                    <td>{event.entity_type}{event.entity_id ? ` ${event.entity_id.slice(0, 8)}` : ""}</td>
                    <td>{event.request_id ? <code>{event.request_id.slice(0, 12)}…</code> : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section className="x-card">
        <div className="x-card-head"><h3>Data and limits</h3></div>
        <ul className="x-ul">
          <li>Uploads: PDF only, 10 MB and 20 pages per file (enforced in UI and backend).</li>
          <li>Extraction: at most 4 model calls per request, 20 per day installation-wide.</li>
          <li>Investigate: at most 1 model call per question; retrieval misses abstain for free.</li>
          <li>Source PDFs stay in private storage; the UI receives bytes only through the authenticated file endpoint.</li>
        </ul>
      </section>
    </>
  );
}
