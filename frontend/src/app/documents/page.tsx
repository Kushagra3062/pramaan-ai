"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/app/AppShell";
import { PageTitle } from "@/components/app/ProductPage";
import { listDocuments, uploadDocument, type DocumentItem } from "@/lib/api/documents";
import { getWorkspaces, type WorkspaceMembership } from "@/lib/api/workspaces";

export default function DocumentsPage() {
  const [workspaces, setWorkspaces] = useState<WorkspaceMembership[]>([]);
  const [workspace, setWorkspace] = useState("");
  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [message, setMessage] = useState("Loading…");
  const [uploading, setUploading] = useState(false);

  const load = async (id: string) => {
    setMessage("Loading documents…");
    const r = await listDocuments(id);
    if (r.ok) {
      setDocs(r.data.items);
      setMessage("");
    } else {
      setMessage(r.message);
    }
  };

  useEffect(() => {
    void getWorkspaces().then((r) => {
      if (!r.ok) return setMessage(r.message);
      setWorkspaces(r.data);
      if (r.data[0]) {
        setWorkspace(r.data[0].workspace_id);
        void load(r.data[0].workspace_id);
      } else {
        setMessage("No workspace access. Ask an administrator to add you.");
      }
    });
  }, []);

  async function upload(file: File) {
    if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
      setMessage("Choose a PDF file.");
      return;
    }
    if (!workspace) return;
    setUploading(true);
    const r = await uploadDocument(workspace, file);
    setUploading(false);
    if (r.ok) {
      setMessage(`Upload accepted: ${r.data.processing_status}`);
      void load(workspace);
    } else {
      setMessage(r.message);
    }
  }

  return (
    <AppShell>
      <PageTitle eyebrow="Document library" title="Documents"><p>Upload PDFs and inspect their real processing state.</p></PageTitle>
      <div className="x-card">
        <div className="doc-toolbar">
          <label className="workspace-select">Workspace<select value={workspace} onChange={(e) => { setWorkspace(e.target.value); void load(e.target.value); }}>{workspaces.map((w) => <option value={w.workspace_id} key={w.workspace_id}>{w.name} · {w.role}</option>)}</select></label>
          <div className="doc-toolbar-actions">
            <label className="secondary-button">{uploading ? "Uploading…" : "Upload PDF"}<input hidden type="file" accept="application/pdf" disabled={uploading || !workspace} onChange={(e) => e.target.files?.[0] && void upload(e.target.files[0])} /></label>
            <button className="refresh-button" onClick={() => workspace && void load(workspace)}>Refresh</button>
          </div>
        </div>
        {message && <p className="x-banner x-banner-info">{message}</p>}
      </div>
      <section className="x-list">
        {docs.map((d) => (
          <Link className="x-item doc-row" href={`/documents/${d.id}?workspace=${encodeURIComponent(workspace)}`} key={d.id}>
            <div className="x-item-head doc-row-head">
              <span className="doc-row-title">{d.original_filename}</span>
              <span className="x-badge x-badge-info">{d.processing_status}</span>
            </div>
            <span className="x-muted doc-row-meta">{d.page_count ?? "—"} pages · {new Date(d.created_at).toLocaleString()}</span>
          </Link>
        ))}
        {!message && !docs.length && <div className="product-empty"><h2>No documents</h2><p>Upload a PDF to this workspace. OCR and structured extraction remain explicit separate actions.</p></div>}
      </section>
    </AppShell>
  );
}
