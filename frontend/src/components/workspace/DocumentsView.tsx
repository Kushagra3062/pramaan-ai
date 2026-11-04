"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { PageTitle } from "@/components/app/ProductPage";
import { EmptyCard, ErrorCard, LoadRow, StatusPill, formatDate } from "@/components/workspace/ui";
import { listDocuments, type DocumentItem } from "@/lib/api/documents";

const PAGE_SIZE = 20;
const STATUSES = ["", "READY", "PROCESSING", "UPLOADED", "NEEDS_OCR", "FAILED"];

export function DocumentsView({ workspaceId }: Readonly<{ workspaceId: string }>) {
  const [items, setItems] = useState<DocumentItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const root = `/w/${workspaceId}`;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    void listDocuments(workspaceId, offset, status).then((result) => {
      if (cancelled) return;
      setLoading(false);
      if (!result.ok) return setError(result.message);
      setItems(result.data.items);
      setTotal(result.data.total);
    });
    return () => { cancelled = true; };
  }, [workspaceId, offset, status, attempt]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return items;
    return items.filter((doc) => doc.original_filename.toLowerCase().includes(needle));
  }, [items, query]);

  const toggle = (id: string) => setSelected((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });

  return (
    <>
      <PageTitle eyebrow="Document explorer" title="Documents">
        <p>Keyword search, status filters and paginated browsing. Semantic search lives in Investigate.</p>
      </PageTitle>
      <div className="doc-toolbar">
        <div className="x-filters">
          <label className="w-field">Search title
            <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter by filename…" />
          </label>
          <label className="w-field">Status
            <select value={status} onChange={(event) => { setOffset(0); setStatus(event.target.value); }}>
              {STATUSES.map((value) => <option key={value} value={value}>{value === "" ? "All statuses" : value}</option>)}
            </select>
          </label>
        </div>
        <div className="doc-toolbar-actions">
          {selected.size > 0 ? <Link className="secondary-button" href={`${root}/investigate?docs=${[...selected].join(",")}`}>Investigate selection ({selected.size})</Link> : null}
          <Link className="primary-button" href={`${root}/documents/upload`}>Upload PDF</Link>
        </div>
      </div>
      {error ? <ErrorCard title="Could not load documents" message={error} onRetry={() => setAttempt((n) => n + 1)} /> : null}
      {!error && loading ? <LoadRow text="Loading documents…" /> : null}
      {!error && !loading && visible.length === 0 ? <EmptyCard title="No documents match" detail="Upload a PDF or clear the filters." href={`${root}/documents/upload`} action="Upload a document" /> : null}
      {!error && !loading && visible.length > 0 ? (
        <div className="x-list">
          {visible.map((doc) => (
            <div key={doc.id} className="x-item w-doc-row">
              <input type="checkbox" checked={selected.has(doc.id)} onChange={() => toggle(doc.id)} aria-label={`Select ${doc.original_filename}`} />
              <Link className="w-doc-main" href={`${root}/documents/${doc.id}`}>
                <span className="doc-row-title">{doc.original_filename}</span>
                <span className="doc-row-meta x-muted">{formatDate(doc.created_at)} · {doc.page_count ?? "—"} pages · {(doc.file_size_bytes / 1024).toFixed(1)} KB · {doc.detected_language ?? "language unknown"}</span>
              </Link>
              <StatusPill value={doc.processing_status} />
            </div>
          ))}
        </div>
      ) : null}
      <div className="x-pager">
        <button type="button" className="secondary-button x-small-button" disabled={offset === 0} onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}>← Newer</button>
        <span className="x-muted">{total === 0 ? "0" : `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} of ${total}`}</span>
        <button type="button" className="secondary-button x-small-button" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset((value) => value + PAGE_SIZE)}>Older →</button>
      </div>
    </>
  );
}
