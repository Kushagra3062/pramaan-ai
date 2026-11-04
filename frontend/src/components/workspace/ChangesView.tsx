"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { PageTitle } from "@/components/app/ProductPage";
import { EmptyCard, ErrorCard, LoadRow, StatusPill, formatDate, shortId } from "@/components/workspace/ui";
import { detectConflicts, dismissConflict, listConflicts, resolveConflict, type ConflictItem, type DetectionResult } from "@/lib/api/conflicts";
import { getComparison, listDocuments, type DocumentItem, type PageComparison } from "@/lib/api/documents";
import { getImpact, listGraphNodes, type GraphNode, type ImpactResult } from "@/lib/api/graph";

type Tab = "conflicts" | "versions" | "impact";

function ConflictCard({ item, workspaceId, onChanged }: Readonly<{ item: ConflictItem; workspaceId: string; onChanged: () => void }>) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const root = `/w/${workspaceId}`;
  const act = async (action: "resolve" | "dismiss") => {
    if (!note.trim() || busy) return;
    setBusy(true);
    setError("");
    const result = action === "resolve" ? await resolveConflict(workspaceId, item.id, note.trim()) : await dismissConflict(workspaceId, item.id, note.trim());
    setBusy(false);
    if (!result.ok) return setError(result.message);
    onChanged();
  };
  const sides = [item.fact_a, item.fact_b] as const;
  return (
    <div className="x-item">
      <div className="x-item-head">
        <StatusPill value={item.status} />
        <StatusPill value={item.severity} />
        <span className="x-badge x-badge-muted">{item.conflict_type.replace(/_/g, " ")}</span>
        <span className="x-muted x-note">{formatDate(item.created_at)}</span>
      </div>
      <p className="x-note x-muted">{item.explanation}</p>
      <div className="w-sides">
        {sides.map((side, index) => (
          <div key={side.fact_id} className="w-side">
            <p className="x-label">Side {index === 0 ? "A" : "B"} · {side.fact_type}</p>
            <p className="x-value">{side.raw_value}</p>
            <blockquote className="x-quote" lang={undefined}>“{side.source_quote}”</blockquote>
            <p className="x-note x-muted">{side.document_filename} · page {side.page_number ?? "—"}</p>
            <p><Link className="text-link" href={`${root}/documents/${side.document_id}`}>Open source →</Link></p>
          </div>
        ))}
      </div>
      {item.status === "OPEN" || item.status === "IN_REVIEW" ? (
        <div className="w-resolve">
          <label className="w-field">Resolution note (required)<input type="text" value={note} maxLength={2000} onChange={(event) => setNote(event.target.value)} placeholder="Why this is resolved, dismissed, or explained by scope…" /></label>
          <div className="x-actions">
            <button type="button" className="primary-button x-small-button" disabled={!note.trim() || busy} onClick={() => void act("resolve")}>Mark resolved</button>
            <button type="button" className="secondary-button x-small-button" disabled={!note.trim() || busy} onClick={() => void act("dismiss")}>Dismiss</button>
          </div>
          {error ? <p className="x-banner x-banner-err">{error}</p> : null}
        </div>
      ) : (
        <p className="x-note x-muted">Closed{formatDate(item.resolved_at) === "—" ? "" : ` · ${formatDate(item.resolved_at)}`}{item.resolution_note ? ` — ${item.resolution_note}` : ""}. Original facts are preserved.</p>
      )}
    </div>
  );
}

function ImpactGraph({ impact }: Readonly<{ impact: ImpactResult }>) {
  const width = 640;
  const height = 300;
  const center = { x: width / 2, y: height / 2 };
  const others = impact.nodes.filter((node) => node.id !== impact.start_node_id).slice(0, 11);
  const positions = new Map<string, { x: number; y: number }>([[impact.start_node_id, center]]);
  others.forEach((node, index) => {
    const angle = (2 * Math.PI * index) / Math.max(others.length, 1) - Math.PI / 2;
    positions.set(node.id, { x: center.x + 220 * Math.cos(angle), y: center.y + 110 * Math.sin(angle) });
  });
  const label = (id: string) => impact.nodes.find((node) => node.id === id)?.display_label.slice(0, 22) ?? shortId(id);
  return (
    <svg className="w-graph" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Impact graph visualization">
      {impact.edges.map((edge) => {
        const from = positions.get(edge.source_node_id);
        const to = positions.get(edge.target_node_id);
        if (!from || !to) return null;
        return <line key={edge.id} x1={from.x} y1={from.y} x2={to.x} y2={to.y} className={edge.validated ? "w-edge-valid" : "w-edge-candidate"} />;
      })}
      {[...positions.entries()].map(([id, point]) => (
        <g key={id}>
          <circle cx={point.x} cy={point.y} r={id === impact.start_node_id ? 10 : 7} className={id === impact.start_node_id ? "w-node-start" : "w-node"} />
          <text x={point.x} y={point.y + 24} textAnchor="middle" className="w-node-label">{label(id)}</text>
        </g>
      ))}
    </svg>
  );
}

export function ChangesView({ workspaceId }: Readonly<{ workspaceId: string }>) {
  const search = useSearchParams();
  const [tab, setTab] = useState<Tab>("conflicts");
  const [conflicts, setConflicts] = useState<ConflictItem[]>([]);
  const [status, setStatus] = useState("");
  const [detecting, setDetecting] = useState(false);
  const [summary, setSummary] = useState<DetectionResult | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [compareDoc, setCompareDoc] = useState("");
  const [comparisons, setComparisons] = useState<PageComparison[] | null>(null);
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [impactNode, setImpactNode] = useState("");
  const [impact, setImpact] = useState<ImpactResult | null>(null);
  const [impactError, setImpactError] = useState("");

  useEffect(() => {
    const requested = search.get("document");
    if (requested) {
      setTab("versions");
      setCompareDoc(requested);
    }
  }, [search]);

  useEffect(() => {
    let cancelled = false;
    setError("");
    void listConflicts(workspaceId, status, 30).then((result) => {
      if (cancelled) return;
      if (!result.ok) return setError(result.message);
      setConflicts(result.data.items);
    });
    return () => { cancelled = true; };
  }, [workspaceId, status, attempt]);

  useEffect(() => {
    void listDocuments(workspaceId, 0).then((result) => { if (result.ok) setDocuments(result.data.items); });
    void listGraphNodes(workspaceId, 50).then((result) => { if (result.ok) setNodes(result.data.items); });
  }, [workspaceId]);

  useEffect(() => {
    if (tab !== "versions" || !compareDoc) return;
    setComparisons(null);
    void getComparison(workspaceId, compareDoc).then((result) => { if (result.ok) setComparisons(result.data); });
  }, [tab, compareDoc, workspaceId]);

  const detect = async () => {
    if (detecting) return;
    setDetecting(true);
    setError("");
    const result = await detectConflicts(workspaceId);
    setDetecting(false);
    if (!result.ok) return setError(result.message);
    setSummary(result.data);
    setAttempt((n) => n + 1);
  };

  const runImpact = async () => {
    if (!impactNode) return;
    setImpactError("");
    setImpact(null);
    const result = await getImpact(workspaceId, impactNode, 2);
    if (!result.ok) return setImpactError(result.message);
    setImpact(result.data);
  };

  return (
    <>
      <PageTitle eyebrow="Changes and conflicts" title="Changes">
        <p>Cross-document conflicts, extraction comparisons and bounded impact paths — every side traceable to source.</p>
      </PageTitle>
      <div className="w-tabs" role="tablist" aria-label="Changes views">
        {(["conflicts", "versions", "impact"] as const).map((value) => (
          <button key={value} role="tab" aria-selected={tab === value} type="button" className={tab === value ? "w-tab active" : "w-tab"} onClick={() => setTab(value)}>
            {value === "conflicts" ? "Conflicts" : value === "versions" ? "Version changes" : "Impact graph"}
          </button>
        ))}
      </div>
      {tab === "conflicts" ? (
        <>
          <div className="x-actions">
            <button type="button" className="primary-button" disabled={detecting} onClick={() => void detect()}>{detecting ? "Detecting…" : "Run deterministic detection"}</button>
            <label className="w-field">Status
              <select value={status} onChange={(event) => setStatus(event.target.value)}>
                <option value="">All</option><option value="OPEN">Open</option><option value="IN_REVIEW">In review</option><option value="RESOLVED">Resolved</option><option value="DISMISSED">Dismissed</option>
              </select>
            </label>
          </div>
          {summary ? <p className="x-banner x-banner-info">Scanned {summary.facts_scanned} facts · compared {summary.pairs_compared} pairs · created {summary.candidates_created} candidates · skipped {summary.candidates_skipped_existing} existing.</p> : null}
          {error ? <ErrorCard title="Conflict data unavailable" message={error} onRetry={() => setAttempt((n) => n + 1)} /> : null}
          {!error && conflicts.length === 0 ? <EmptyCard title="No conflicts here" detail="Run detection after extracting facts from at least two documents." /> : null}
          <div className="x-list">{conflicts.map((item) => <ConflictCard key={item.id} item={item} workspaceId={workspaceId} onChanged={() => setAttempt((n) => n + 1)} />)}</div>
          <p className="x-note x-muted">Resolution records a human-selected outcome with an audit event. The system never infers legal priority or picks a winner automatically.</p>
        </>
      ) : null}
      {tab === "versions" ? (
        <>
          <label className="w-field">Document
            <select value={compareDoc} onChange={(event) => setCompareDoc(event.target.value)}>
              <option value="">Select a document…</option>
              {documents.map((doc) => <option key={doc.id} value={doc.id}>{doc.original_filename}</option>)}
            </select>
          </label>
          {!compareDoc ? <EmptyCard title="Select a document" detail="Page-level extraction comparison appears here." /> : null}
          {compareDoc && comparisons === null ? <LoadRow text="Loading extraction comparison…" /> : null}
          {compareDoc && comparisons !== null && comparisons.length === 0 ? <EmptyCard title="No comparison data" detail="Run OCR in compare mode from the document page first." /> : null}
          {comparisons !== null && comparisons.length > 0 ? (
            <div className="x-list">{comparisons.map((page) => (
              <div key={page.page_number} className="x-item">
                <div className="x-item-head"><strong>Page {page.page_number}</strong><span className="x-badge x-badge-info">canonical: {page.canonical_method}</span></div>
                <div className="w-sides">
                  <div className="w-side"><p className="x-label">Tesseract</p><p className="x-excerpt">{page.tesseract_attempt?.extracted_text?.slice(0, 600) || "No attempt recorded."}</p></div>
                  <div className="w-side"><p className="x-label">Meta Vision</p><p className="x-excerpt">{page.meta_vision_attempt?.extracted_text?.slice(0, 600) || "No attempt recorded."}</p></div>
                </div>
                <p className="x-note x-muted">Scan quality and unmatched pages surface here; canonical selection stays on the document page.</p>
              </div>
            ))}</div>
          ) : null}
        </>
      ) : null}
      {tab === "impact" ? (
        <>
          <div className="x-actions">
            <label className="w-field">Start node
              <select value={impactNode} onChange={(event) => setImpactNode(event.target.value)}>
                <option value="">Select a graph node…</option>
                {nodes.map((node) => <option key={node.id} value={node.id}>{node.display_label} ({node.node_type})</option>)}
              </select>
            </label>
            <button type="button" className="primary-button" disabled={!impactNode} onClick={() => void runImpact()}>Compute impact (depth ≤ 2)</button>
          </div>
          {nodes.length === 0 ? <EmptyCard title="Graph is empty" detail="Graph nodes are created as facts, requirements, conflicts and actions accumulate." /> : null}
          {impactError ? <p className="x-banner x-banner-err">{impactError}</p> : null}
          {impact ? (
            <>
              <section className="x-card">
                <div className="x-card-head"><h3>Impact map</h3><span className="x-muted x-note">{impact.reached_nodes} nodes · {impact.edges.length} edges{impact.truncated ? " · truncated by cap" : ""}</span></div>
                <ImpactGraph impact={impact} />
                <p className="x-note x-muted"><span className="w-legend-valid">—</span> validated path (deterministic/human-confirmed) · <span className="w-legend-candidate">- -</span> candidate path (model-proposed, shown separately)</p>
              </section>
              <section className="w-columns">
                <div className="x-card">
                  <div className="x-card-head"><h3>Validated paths</h3><span className="x-muted x-note">{impact.validated_paths.length}</span></div>
                  {impact.validated_paths.length === 0 ? <p className="x-note x-muted">None.</p> : (
                    <ul className="x-ul">{impact.validated_paths.slice(0, 12).map((path, index) => <li key={path.edge_ids.join("-") || index}><code>{path.node_ids.map((id) => shortId(id)).join(" → ")}</code></li>)}</ul>
                  )}
                </div>
                <div className="x-card">
                  <div className="x-card-head"><h3>Candidate paths</h3><span className="x-muted x-note">{impact.candidate_paths.length}</span></div>
                  {impact.candidate_paths.length === 0 ? <p className="x-note x-muted">None.</p> : (
                    <ul className="x-ul">{impact.candidate_paths.slice(0, 12).map((path, index) => <li key={path.edge_ids.join("-") || index}><code>{path.node_ids.map((id) => shortId(id)).join(" → ")}</code></li>)}</ul>
                  )}
                </div>
              </section>
            </>
          ) : null}
        </>
      ) : null}
    </>
  );
}
