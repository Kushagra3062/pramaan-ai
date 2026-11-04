"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { AppShell } from "@/components/app/AppShell";
import { PageTitle } from "@/components/app/ProductPage";
import { getChunks, getDocument, getPages, type ChunkItem, type DocumentItem, type PageItem } from "@/lib/api/documents";
import { ExtractionPanel } from "@/components/extraction/ExtractionPanel";
import { OcrPanel } from "@/components/extraction/OcrPanel";

export default function DocumentDetailPage() {
  const params = useParams<{ id: string }>();
  const query = useSearchParams();
  const workspace = query.get("workspace") || "";
  const [doc, setDoc] = useState<DocumentItem | null>(null);
  const [pages, setPages] = useState<PageItem[]>([]);
  const [chunks, setChunks] = useState<ChunkItem[]>([]);
  const [message, setMessage] = useState("Loading document…");

  const load = useCallback(async () => {
    const [d, p, c] = await Promise.all([
      getDocument(workspace, params.id),
      getPages(workspace, params.id),
      getChunks(workspace, params.id),
    ]);
    if (!d.ok) {
      setMessage(d.message);
      return;
    }
    setDoc(d.data);
    if (p.ok) setPages(p.data);
    if (c.ok) setChunks(c.data);
    setMessage("");
  }, [workspace, params.id]);

  useEffect(() => {
    if (!workspace) {
      setMessage("Choose this document from a workspace document list.");
      return;
    }
    setMessage("Loading document…");
    void load();
  }, [workspace, load]);

  const needsOcr = (doc?.processing_status === "NEEDS_OCR") || pages.some((p) => p.extraction_status === "NEEDS_OCR");
  const pendingPages = pages.filter((p) => p.extraction_status === "NEEDS_OCR").length;

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
          <OcrPanel workspaceId={workspace} doc={doc} needsOcr={needsOcr} pendingPages={pendingPages} onFinished={() => void load()} />
          {doc && (
            <ExtractionPanel
              workspaceId={workspace}
              doc={{ id: doc.id, original_filename: doc.original_filename, processing_status: doc.processing_status, page_count: doc.page_count }}
              pages={pages}
              chunks={chunks}
              active
              hasEvidence={false}
              onExtractionFinished={() => void load()}
            />
          )}
        </>
      )}
    </AppShell>
  );
}
