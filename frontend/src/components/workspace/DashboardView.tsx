"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PageTitle } from "@/components/app/ProductPage";
import { EmptyCard, ErrorCard, LoadRow, StatusPill, formatDate } from "@/components/workspace/ui";
import { listActions } from "@/lib/api/actions";
import { listAnalyses, type AnalysisSummary } from "@/lib/api/analyses";
import { listConflicts, type ConflictItem } from "@/lib/api/conflicts";
import { listDocuments, type DocumentItem } from "@/lib/api/documents";
import { getOverview, type WorkspaceOverview } from "@/lib/api/workspaces";
import type { ActionItem } from "@/lib/api/actions";

export function DashboardView({ workspaceId }: Readonly<{ workspaceId: string }>) {
  const [overview, setOverview] = useState<WorkspaceOverview | null>(null);
  const [conflicts, setConflicts] = useState<ConflictItem[]>([]);
  const [actions, setActions] = useState<ActionItem[]>([]);
  const [packets, setPackets] = useState<AnalysisSummary[]>([]);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [attempt, setAttempt] = useState(0);
  const root = `/w/${workspaceId}`;

  const load = useCallback(() => {
    let cancelled = false;
    setError("");
    setOverview(null);
    void (async () => {
      const [ov, cf, ac, an, dc] = await Promise.all([
        getOverview(workspaceId),
        listConflicts(workspaceId, "OPEN", 5),
        listActions(workspaceId, "PROPOSED", 5),
        listAnalyses(workspaceId, 5),
        listDocuments(workspaceId, 0),
      ]);
      if (cancelled) return;
      if (!ov.ok) return setError(ov.message);
      setOverview(ov.data);
      if (cf.ok) setConflicts(cf.data.items);
      if (ac.ok) setActions(ac.data.items);
      if (an.ok) setPackets(an.data.items);
      if (dc.ok) setDocuments(dc.data.items.slice(0, 5));
      setUpdatedAt(new Date());
    })();
    return () => { cancelled = true; };
  }, [workspaceId]);

  useEffect(load, [load, attempt]);

  if (error) {
    return (
      <>
        <PageTitle eyebrow="Workspace dashboard" title="Overview" />
        <ErrorCard title="Could not load overview" message={error} onRetry={() => setAttempt((n) => n + 1)} />
      </>
    );
  }
  if (!overview) {
    return (
      <>
        <PageTitle eyebrow="Workspace dashboard" title="Overview" />
        <LoadRow text="Loading live workspace data…" />
      </>
    );
  }
  const cards: [string, number, string][] = [
    ["Documents", overview.total_documents, "Across this workspace"],
    ["Ready", overview.ready_documents, "Available to inspect"],
    ["In progress", overview.processing_documents, "Awaiting completion"],
    ["Failed", overview.failed_documents, "Needs attention"],
    ["Extracted facts", overview.extracted_facts, "Source-linked proposals"],
    ["Requirements", overview.requirements, "Draft and active"],
    ["Pending reviews", overview.pending_reviews, "Human review queue"],
    ["Open conflicts", conflicts.length, "Top 5 shown below"],
  ];
  return (
    <>
      <PageTitle eyebrow="Workspace dashboard" title="Overview">
        <p>Counts are queried from stored records. {updatedAt ? <>Last updated {updatedAt.toLocaleTimeString()} · <button type="button" className="text-link" onClick={() => setAttempt((n) => n + 1)}>Refresh</button></> : null}</p>
      </PageTitle>
      <section className="stats-grid">{cards.map(([label, value, detail]) => <article className="stat-card" key={label}><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>)}</section>
      <section className="w-columns">
        <div className="x-card">
          <div className="x-card-head"><h3>Open conflicts</h3><Link className="text-link" href={`${root}/changes`}>Open Changes →</Link></div>
          {conflicts.length === 0 ? <EmptyCard title="No open conflicts" detail="Run detection from the Changes page after extracting facts." /> : (
            <div className="x-list">{conflicts.map((item) => (
              <Link key={item.id} className="x-item w-row-link" href={`${root}/changes`}>
                <div className="x-item-head"><StatusPill value={item.severity} /><span className="x-badge x-badge-muted">{item.conflict_type.replace(/_/g, " ")}</span></div>
                <p className="x-title">{item.fact_a.raw_value} ↔ {item.fact_b.raw_value}</p>
                <p className="x-note x-muted">{item.fact_a.document_filename} · {item.fact_b.document_filename}</p>
              </Link>
            ))}</div>
          )}
        </div>
        <div className="x-card">
          <div className="x-card-head"><h3>Action review queue</h3><Link className="text-link" href={`${root}/actions`}>Open Actions →</Link></div>
          {actions.length === 0 ? <EmptyCard title="No pending drafts" detail="Proposals appear here for human approval. Nothing executes automatically." /> : (
            <div className="x-list">{actions.map((item) => (
              <Link key={item.id} className="x-item w-row-link" href={`${root}/actions`}>
                <div className="x-item-head"><StatusPill value={item.priority} /><StatusPill value={item.status} /></div>
                <p className="x-note">{item.description}</p>
              </Link>
            ))}</div>
          )}
        </div>
      </section>
      <section className="w-columns">
        <div className="x-card">
          <div className="x-card-head"><h3>Recent documents</h3><Link className="text-link" href={`${root}/documents`}>Open explorer →</Link></div>
          {documents.length === 0 ? <EmptyCard title="No documents yet" detail="Upload your first PDF to begin." href={`${root}/documents/upload`} action="Upload a document" /> : (
            <div className="x-list">{documents.map((doc) => (
              <Link key={doc.id} className="x-item w-row-link" href={`${root}/documents/${doc.id}`}>
                <div className="x-item-head"><StatusPill value={doc.processing_status} /><span className="x-muted x-note">{formatDate(doc.created_at)}</span></div>
                <p className="x-title">{doc.original_filename}</p>
              </Link>
            ))}</div>
          )}
        </div>
        <div className="x-card">
          <div className="x-card-head"><h3>Recent Decision Packets</h3><Link className="text-link" href={`${root}/investigate`}>Ask a question →</Link></div>
          {packets.length === 0 ? <EmptyCard title="No analyses yet" detail="Investigate turns retrieved evidence into a traceable packet." /> : (
            <div className="x-list">{packets.map((packet) => (
              <Link key={packet.id} className="x-item w-row-link" href={`${root}/packets/${packet.id}`}>
                <div className="x-item-head"><StatusPill value={packet.status} /><span className="x-muted x-note">{formatDate(packet.created_at)}</span></div>
                <p className="x-note">{packet.question}</p>
              </Link>
            ))}</div>
          )}
        </div>
      </section>
      <section className="x-card">
        <div className="x-card-head"><h3>Quick actions</h3></div>
        <div className="x-actions">
          <Link className="primary-button" href={`${root}/documents/upload`}>Upload document</Link>
          <Link className="secondary-button" href={`${root}/investigate`}>Ask a question</Link>
          <Link className="secondary-button" href={`${root}/changes`}>Review conflicts</Link>
          <Link className="secondary-button" href={`${root}/evaluation`}>Run benchmark</Link>
        </div>
      </section>
    </>
  );
}
