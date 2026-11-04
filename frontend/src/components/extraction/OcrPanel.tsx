"use client";

import { useEffect, useRef, useState } from "react";
import { runOcr, type OcrProvider, type OcrRunResult } from "@/lib/api/documents";
import { EvidenceLockNotice } from "./ExtractionPanel";
import { Loading } from "./ListControls";
import { StatusBadge } from "./StatusBadge";

const PROVIDERS: ReadonlyArray<{ value: OcrProvider; label: string; hint: string }> = [
  { value: "meta_vision", label: "Meta Vision (recommended)", hint: "AI transcription; uses model credits. Falls back to Tesseract if the model call fails." },
  { value: "tesseract", label: "Tesseract (local, free)", hint: "On-server OCR, English + Malayalam. No model credits." },
];

export function OcrPanel({
  workspaceId,
  doc,
  needsOcr,
  pendingPages,
  onFinished,
}: {
  workspaceId: string;
  doc: { id: string; original_filename: string; processing_status: string } | null;
  needsOcr: boolean;
  pendingPages: number;
  onFinished: () => void;
}) {
  const [provider, setProvider] = useState<OcrProvider>("meta_vision");
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState<OcrRunResult | null>(null);
  const [error, setError] = useState<{ message: string; httpStatus: number | null } | null>(null);
  const lockRef = useRef(false);

  const docId = doc?.id ?? null;
  useEffect(() => {
    setResult(null);
    setError(null);
  }, [docId, provider]);

  useEffect(() => {
    if (!running) return;
    const started = Date.now();
    setElapsed(0);
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [running]);

  async function handleRun() {
    if (lockRef.current || !doc || !needsOcr) return;
    lockRef.current = true;
    setRunning(true);
    setResult(null);
    setError(null);
    try {
      // Exactly one POST; never retried automatically.
      const response = await runOcr(workspaceId, doc.id, {
        provider,
        fallback_to_tesseract: true,
        languages: "eng+mal",
        dpi: 250,
      });
      if (response.ok) {
        setResult(response.data);
        onFinished();
      } else {
        setError({ message: response.message, httpStatus: response.status });
      }
    } finally {
      lockRef.current = false;
      setRunning(false);
    }
  }

  if (!doc) {
    return <div className="x-card x-empty">Select a document to run text recognition.</div>;
  }

  const ready = doc.processing_status === "READY";
  const failedPages = result ? result.results.filter((r) => r.extraction_status !== "COMPLETED").map((r) => r.page_number) : [];
  const activeProvider = PROVIDERS.find((p) => p.value === provider);

  return (
    <section className="x-card" aria-label="Text recognition">
      <div className="x-card-head">
        <h3>Text recognition (OCR)</h3>
        <StatusBadge tone={ready ? "ok" : needsOcr ? "warn" : "info"}>{doc.processing_status}</StatusBadge>
      </div>
      {!needsOcr ? (
        <div className="x-banner x-banner-info" role="status">
          No pages need OCR. Canonical text is already recorded for this document.
        </div>
      ) : (
        <div className="x-banner x-banner-warn" role="status">
          {pendingPages > 0 ? `${pendingPages} page${pendingPages === 1 ? "" : "s"} need${pendingPages === 1 ? "s" : ""} text recognition before facts can be extracted.` : "This document needs text recognition before facts can be extracted."}
        </div>
      )}
      <div className="x-filters">
        <label>
          <span className="x-label">Provider</span>
          <select value={provider} disabled={running} onChange={(e) => setProvider(e.target.value as OcrProvider)}>
            {PROVIDERS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
          </select>
        </label>
      </div>
      {activeProvider && <p className="x-muted x-note">{activeProvider.hint}</p>}
      <div className="x-actions">
        <button type="button" className="primary-button" onClick={() => void handleRun()} disabled={running || !needsOcr} aria-busy={running}>
          {running ? "Recognizing text…" : "Run text recognition"}
        </button>
        <span className="x-muted x-note">Runs only when you click. Recognized text becomes the canonical source for chunks and extraction.</span>
      </div>
      {running && (
        <div className="x-banner x-banner-info" role="status">
          <Loading label={`Recognizing text… ${elapsed}s elapsed`} />
          <div className="x-note">This may take several minutes for scanned pages. Leaving this page does not cancel the server-side run.</div>
        </div>
      )}
      {result && (
        <div className="x-banner x-banner-ok" role="status">
          {result.message} Pages processed: {result.pages_processed} · Chunks regenerated: {result.chunks_regenerated} · Status: {result.processing_status}
          {failedPages.length > 0 && ` · Pages still incomplete: ${failedPages.join(", ")}`}
        </div>
      )}
      {error && (
        <div className="x-banner x-banner-err" role="alert">
          {error.httpStatus !== null && <strong>HTTP {error.httpStatus} · </strong>}
          {error.message}
        </div>
      )}
      {error?.httpStatus === 409 && <EvidenceLockNotice />}
    </section>
  );
}
