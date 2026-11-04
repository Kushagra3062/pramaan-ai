"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PageTitle } from "@/components/app/ProductPage";
import { ExtractionPanel } from "@/components/extraction/ExtractionPanel";
import { OcrPanel } from "@/components/extraction/OcrPanel";
import { EmptyCard, ErrorCard, LoadRow, StatusPill, formatDate } from "@/components/workspace/ui";
import { getChunks, getDocument, getDocumentFileUrl, getPages, type ChunkItem, type DocumentItem, type PageItem } from "@/lib/api/documents";

function PdfViewer({ workspaceId, documentId }: Readonly<{ workspaceId: string; documentId: string }>) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    void getDocumentFileUrl(workspaceId, documentId).then((result) => {
      if (cancelled) return;
      if (!result.ok) return setError(result.message);
      objectUrl = result.data;
      setUrl(result.data);
    });
    return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [workspaceId, documentId]);
  if (error) return <EmptyCard title="PDF preview unavailable" detail="The extracted text below remains fully readable." />;
  if (!url) return <LoadRow text="Loading private PDF preview…" />;
  return <embed className="w-pdf" src={url} type="application/pdf" aria-label="Source PDF preview" />;
}

export function DocumentDetailView({ workspaceId, documentId }: Readonly<{ workspaceId: string; documentId: string }>) {
  const [doc, setDoc] = useState<DocumentItem | null>(null);
  const [pages, setPages] = useState<PageItem[]>([]);
  const [chunks, setChunks] = useState<ChunkItem[]>([]);
  const [message, setMessage] = useState("Loading document…");
  const [refresh, setRefresh] = useState(0);
  const root = `/w/${workspaceId}`;

  const load = useCallback(async () => {
    const [d, p, c] = await Promise.all([getDocument(workspaceId, documentId), getPages(workspaceId, documentId), getChunks(workspaceId, documentId)]);
    if (!d.ok) {
      setMessage(d.message);
      return;
    }
    setDoc(d.data);
    if (p.ok) setPages(p.data);
    if (c.ok) setChunks(c.data);
    setMessage("");
  }, [workspaceId, documentId]);

  useEffect(() => {
    setMessage("Loading document…");
    void load();
  }, [load, refresh]);

  if (message || !doc) {
    return (
      <>
        <PageTitle eyebrow="Document detail" title="Document" />
        {message === "Loading document…" ? <LoadRow text={message} /> : <ErrorCard title="Could not load document" message={message} onRetry={() => setRefresh((n) => n + 1)} />}
      </>
    );
  }
  const needsOcr = doc.processing_status === "NEEDS_OCR" || pages.some((page) => page.extraction_status === "NEEDS_OCR");
  const pendingPages = pages.filter((page) => page.extraction_status === "NEEDS_OCR").length;
  const emptyPages = pages.filter((page) => !page.extracted_text || page.extracted_text.trim().length === 0);

  return (
    <>
      <PageTitle eyebrow="Document detail" title={doc.original_filename}>
        <p><StatusPill value={doc.processing_status} /> · {formatDate(doc.created_at)} · {doc.detected_language ?? "language unknown"} · {doc.page_count ?? pages.length} pages</p>
      </PageTitle>
      <div className="x-actions">
        <Link className="secondary-button" href={`${root}/investigate?docs=${doc.id}`}>Ask about this document</Link>
        <Link className="secondary-button" href={`${root}/changes?document=${doc.id}`}>Compare versions</Link>
      </div>
      {emptyPages.length > 0 ? <p className="x-banner x-banner-warn">Page quality: {emptyPages.length} page(s) have no extracted text ({emptyPages.map((page) => page.page_number).join(", ")}). OCR or a better scan may help.</p> : null}
      <section className="x-card">
        <div className="x-card-head"><h3>Source PDF</h3><span className="x-muted x-note">Server-mediated private view</span></div>
        <PdfViewer workspaceId={workspaceId} documentId={doc.id} />
      </section>
      <section className="x-card">
        <div className="x-card-head"><h3>Extracted text</h3><span className="x-muted x-note">{pages.length} canonical pages</span></div>
        {pages.length === 0 ? <EmptyCard title="No pages yet" detail="Text appears once processing completes." /> : (
          <div className="x-list">
            {pages.map((page) => (
              <details key={page.id} className="x-evidence-item">
                <summary><strong>Page {page.page_number}</strong><StatusPill value={page.extraction_status} /><span className="x-muted">{page.extraction_method}{page.extraction_method === "ocr" || page.extraction_method.includes("tesseract") ? " · OCR origin" : ""}</span></summary>
                <div className="x-evidence-body">
                  {page.extracted_text ? <p className="x-excerpt" lang={doc.detected_language?.startsWith("ml") ? "ml" : undefined}>{page.extracted_text}</p> : <p className="x-note x-muted">No extracted text for this page.</p>}
                </div>
              </details>
            ))}
          </div>
        )}
      </section>
      <section className="x-card">
        <div className="x-card-head"><h3>Related evidence chunks</h3><span className="x-muted x-note">{chunks.length} chunks</span></div>
        {chunks.length === 0 ? <EmptyCard title="No chunks" detail="Chunks are created during processing." /> : (
          <div className="x-table-wrap">
            <table className="x-table">
              <thead><tr><th>#</th><th>Pages</th><th>Excerpt</th></tr></thead>
              <tbody>
                {chunks.slice(0, 30).map((chunk: ChunkItem) => (
                  <tr key={chunk.id}><td>{chunk.chunk_index}</td><td>{chunk.page_start}–{chunk.page_end}</td><td>{chunk.content.slice(0, 140)}{chunk.content.length > 140 ? "…" : ""}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <OcrPanel workspaceId={workspaceId} doc={doc} needsOcr={needsOcr} pendingPages={pendingPages} onFinished={() => setRefresh((n) => n + 1)} />
      <ExtractionPanel
        workspaceId={workspaceId}
        doc={{ id: doc.id, original_filename: doc.original_filename, processing_status: doc.processing_status, page_count: doc.page_count }}
        pages={pages}
        chunks={chunks}
        active
        hasEvidence={false}
        onExtractionFinished={() => setRefresh((n) => n + 1)}
      />
    </>
  );
}
