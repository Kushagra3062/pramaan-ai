"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { PageTitle } from "@/components/app/ProductPage";
import { StatusPill } from "@/components/workspace/ui";
import { getDocument, uploadDocument } from "@/lib/api/documents";

const MAX_MB = 10;
const TERMINAL = new Set(["READY", "FAILED", "DELETED"]);

interface Stage { key: string; label: string; detail: string; state: "done" | "active" | "todo" | "failed"; }

function stagesFor(status: string): Stage[] {
  const order = ["UPLOADED", "PROCESSING", "READY"];
  const failed = status === "FAILED";
  const index = failed ? 1 : order.includes(status) ? order.indexOf(status) : status === "NEEDS_OCR" || status === "VALIDATING" ? 1 : 0;
  return [
    { key: "upload", label: "Uploaded", detail: "PDF validated and stored privately", state: index >= 0 ? "done" : "todo" },
    { key: "process", label: "Extracting text", detail: "Page-aware extraction, OCR when needed", state: failed ? "failed" : index > 1 ? "done" : "active" },
    { key: "ready", label: "Ready", detail: "Pages chunked and available for extraction", state: failed ? "todo" : index >= 2 ? "done" : "todo" },
  ];
}

export function UploadView({ workspaceId }: Readonly<{ workspaceId: string }>) {
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [validation, setValidation] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [documentId, setDocumentId] = useState<string | null>(null);
  const [existingId, setExistingId] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const polls = useRef(0);
  const root = `/w/${workspaceId}`;

  const pick = useCallback((candidate: File | null) => {
    setUploadError("");
    setDocumentId(null);
    setExistingId(null);
    setStatus("");
    if (!candidate) {
      setFile(null);
      setValidation("");
      return;
    }
    if (!candidate.name.toLowerCase().endsWith(".pdf") && candidate.type !== "application/pdf") {
      setFile(null);
      setValidation("Only PDF files are accepted in this release.");
      return;
    }
    if (candidate.size > MAX_MB * 1024 * 1024) {
      setFile(null);
      setValidation(`File exceeds the ${MAX_MB} MB limit enforced by the UI and the backend.`);
      return;
    }
    setFile(candidate);
    setValidation("");
  }, []);

  const submit = useCallback(async () => {
    if (!file || uploading) return;
    setUploading(true);
    setUploadError("");
    const result = await uploadDocument(workspaceId, file);
    setUploading(false);
    if (!result.ok) return setUploadError(result.message);
    if (result.data.existing_document_id) return setExistingId(result.data.existing_document_id);
    if (!result.data.id) return setUploadError("Upload succeeded but returned no document id.");
    polls.current = 0;
    setDocumentId(result.data.id);
    setStatus(result.data.processing_status);
  }, [file, uploading, workspaceId]);

  useEffect(() => {
    if (!documentId || TERMINAL.has(status)) return;
    polls.current += 1;
    const delay = Math.min(3000 * polls.current, 15000);
    const timer = setTimeout(() => {
      void getDocument(workspaceId, documentId).then((result) => {
        if (result.ok) setStatus(result.data.processing_status);
      });
    }, delay);
    return () => clearTimeout(timer);
  }, [documentId, status, workspaceId]);

  const stages = stagesFor(status || "UPLOADED");

  return (
    <>
      <PageTitle eyebrow="Upload and ingestion" title="Upload a document">
        <p>PDF only · up to {MAX_MB} MB · up to 20 pages. Processing status persists — you can leave and return.</p>
      </PageTitle>
      <section className="x-card">
        <div className="x-card-head"><h3>1 · Select a file</h3></div>
        <div
          className={dragging ? "w-dropzone dragging" : "w-dropzone"}
          onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => { event.preventDefault(); setDragging(false); pick(event.dataTransfer.files?.[0] ?? null); }}
          role="button" tabIndex={0} aria-label="Drop a PDF file here or choose one"
          onKeyDown={(event) => { if (event.key === "Enter") document.getElementById("w-file-input")?.click(); }}
        >
          <p className="w-drop-title">{file ? file.name : "Drop a PDF here, or choose a file"}</p>
          <p className="x-note x-muted">Private storage · SHA-256 duplicate detection · server-side signature check</p>
          <input id="w-file-input" type="file" accept="application/pdf,.pdf" hidden onChange={(event) => pick(event.target.files?.[0] ?? null)} />
          <button type="button" className="secondary-button" onClick={() => document.getElementById("w-file-input")?.click()}>Choose file</button>
        </div>
        {validation ? <p className="x-banner x-banner-err">{validation}</p> : null}
        {file ? (
          <dl className="x-meta">
            <div><dt>Name</dt><dd>{file.name}</dd></div>
            <div><dt>Size</dt><dd>{(file.size / 1024).toFixed(1)} KB</dd></div>
            <div><dt>Type</dt><dd>{file.type || "application/pdf"}</dd></div>
          </dl>
        ) : null}
        <div className="x-actions">
          <button type="button" className="primary-button" disabled={!file || uploading} onClick={() => void submit()}>{uploading ? "Uploading…" : "Upload and process"}</button>
          {file ? <button type="button" className="secondary-button" onClick={() => pick(null)}>Remove</button> : null}
        </div>
        {uploadError ? <p className="x-banner x-banner-err">{uploadError}</p> : null}
        {existingId ? (
          <p className="x-banner x-banner-warn">Duplicate detected — this file already exists. <Link className="text-link" href={`${root}/documents/${existingId}`}>Open the existing document →</Link></p>
        ) : null}
      </section>
      {documentId ? (
        <section className="x-card">
          <div className="x-card-head"><h3>2 · Processing timeline</h3><StatusPill value={status || "UPLOADED"} /></div>
          <ol className="w-timeline">
            {stages.map((stage) => <li key={stage.key} data-state={stage.state}><span className="w-dot" aria-hidden="true" /><div><strong>{stage.label}</strong><p>{stage.detail}</p></div></li>)}
          </ol>
          <p className="x-note x-muted">Polling with backoff; stops automatically at a terminal status.</p>
          <div className="x-actions">
            <Link className="primary-button" href={`${root}/documents/${documentId}`}>Open document →</Link>
          </div>
        </section>
      ) : null}
    </>
  );
}
