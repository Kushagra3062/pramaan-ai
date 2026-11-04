"use client";

import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { AppShell } from "@/components/app/AppShell";
import { PageTitle } from "@/components/app/ProductPage";
import { getChunks, getDocument, getPages, type ChunkItem, type DocumentItem, type PageItem } from "@/lib/api/documents";
import { FactsList } from "@/components/extraction/FactsList";
import { RequirementsList } from "@/components/extraction/RequirementsList";

export default function DocumentDetailPage() {
  const params = useParams<{ id: string }>();
  const query = useSearchParams();
  const workspace = query.get("workspace") || "";
  const [doc, setDoc] = useState<DocumentItem | null>(null);
  const [pages, setPages] = useState<PageItem[]>([]);
  const [chunks, setChunks] = useState<ChunkItem[]>([]);
  const [message, setMessage] = useState("Loading document…");

  useEffect(() => {
    if (!workspace) {
      setMessage("Choose this document from a workspace document list.");
      return;
    }
    void Promise.all([getDocument(workspace, params.id), getPages(workspace, params.id), getChunks(workspace, params.id)]).then(([d, p, c]) => {
      if (!d.ok) return setMessage(d.message);
      setDoc(d.data);
      if (p.ok) setPages(p.data);
      if (c.ok) setChunks(c.data);
      setMessage("");
    });
  }, [workspace, params.id]);

  const chunksById = Object.fromEntries(chunks.map((c) => [c.id, c]));

  return (
    <AppShell>
      <PageTitle eyebrow="Document detail" title={doc?.original_filename || "Document"}>
        <p>{doc ? `${doc.processing_status} · ${doc.page_count ?? "—"} pages` : message}</p>
      </PageTitle>
      {message ? (
        <p className="x-banner x-banner-info">{message}</p>
      ) : (
        <>
          <section className="x-card" aria-label="Source record">
            <div className="x-card-head">
              <h3>Source record</h3>
              <span className="x-badge x-badge-info">{doc?.processing_status}</span>
            </div>
            <dl className="x-meta">
              <div>
                <dt>Document ID</dt>
                <dd><code className="doc-id" title={params.id}>{params.id}</code></dd>
              </div>
              <div>
                <dt>Pages</dt>
                <dd>{doc?.page_count ?? pages.length}</dd>
              </div>
              <div>
                <dt>Canonical pages</dt>
                <dd>{pages.length}</dd>
              </div>
              <div>
                <dt>Chunks</dt>
                <dd>{chunks.length}</dd>
              </div>
            </dl>
          </section>
          <section className="x-card" aria-label="Canonical pages">
            <div className="x-card-head">
              <h3>Canonical pages</h3>
              <span className="x-muted x-note">{pages.length} pages</span>
            </div>
            <div className="x-list">
              {pages.map((p) => (
                <div className="x-item doc-page-row" key={p.id}>
                  <span className="doc-page-title">Page {p.page_number}</span>
                  <span className="x-badge x-badge-info">{p.extraction_status}</span>
                  <span className="x-muted">{p.extraction_method}</span>
                </div>
              ))}
            </div>
          </section>
          <section className="x-stack">
            <FactsList workspaceId={workspace} documentId={params.id} chunksById={chunksById} refreshToken={0} active />
            <RequirementsList workspaceId={workspace} documentId={params.id} chunksById={chunksById} refreshToken={0} active />
          </section>
        </>
      )}
    </AppShell>
  );
}
