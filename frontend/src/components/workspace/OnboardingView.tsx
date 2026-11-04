"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { PageTitle } from "@/components/app/ProductPage";
import { ErrorCard, LoadRow } from "@/components/workspace/ui";
import { getWorkspaces, type WorkspaceMembership } from "@/lib/api/workspaces";

export function OnboardingView() {
  const [workspaces, setWorkspaces] = useState<WorkspaceMembership[] | null>(null);
  const [message, setMessage] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setWorkspaces(null);
    setMessage("");
    void getWorkspaces().then((result) => {
      if (cancelled) return;
      if (!result.ok) return setMessage(result.message);
      setWorkspaces(result.data);
    });
    return () => { cancelled = true; };
  }, [attempt]);

  return (
    <main className="app-main" style={{ margin: "0 auto" }}>
      <PageTitle eyebrow="Onboarding" title="Choose a workspace">
        <p>Every document, fact and decision lives inside one workspace. Pick yours to continue.</p>
      </PageTitle>
      {message ? <ErrorCard title="Could not load workspaces" message={message} onRetry={() => setAttempt((n) => n + 1)} /> : null}
      {!message && workspaces === null ? <LoadRow text="Loading your workspaces…" /> : null}
      {!message && workspaces !== null && workspaces.length === 0 ? (
        <div className="product-empty">
          <div className="empty-icon" aria-hidden="true">○</div>
          <h2>No workspace access yet</h2>
          <p>Membership is managed by a workspace administrator. Once added, your workspaces appear here.</p>
          <button type="button" className="secondary-button" onClick={() => setAttempt((n) => n + 1)}>Check again</button>
        </div>
      ) : null}
      {!message && workspaces !== null && workspaces.length > 0 ? (
        <section className="w-grid">
          {workspaces.map((workspace) => (
            <Link key={workspace.workspace_id} className="w-pick" href={`/w/${workspace.workspace_id}`}>
              <span className="w-pick-name">{workspace.name}</span>
              <span className="w-pick-meta">{workspace.role} · {workspace.slug}</span>
              <span className="w-pick-go">Open dashboard →</span>
            </Link>
          ))}
        </section>
      ) : null}
      <section className="x-card">
        <div className="x-card-head"><h3>First-run checklist</h3></div>
        <ol className="x-ul w-checklist">
          <li>Open a workspace and review the dashboard counts — they come from stored records, never guesses.</li>
          <li>Upload a PDF, or open a document to inspect pages, evidence and extraction proposals.</li>
          <li>Ask a question in Investigate and verify one claim against its source passage.</li>
        </ol>
        <p className="x-note x-muted">External model inference processes document text. Prompts, raw text and secrets are never written to logs; Langfuse receives metadata only.</p>
      </section>
    </main>
  );
}
