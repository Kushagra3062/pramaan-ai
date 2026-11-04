"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { extractDocument } from "@/lib/api/extraction";
import type { ExtractionRun } from "@/lib/api/types";
import { FactsList } from "./FactsList";
import type { ChunkPreview } from "./EvidenceList";
import { Loading } from "./ListControls";
import { RequirementsList } from "./RequirementsList";
import { RunSummary } from "./RunSummary";
import { StatusBadge } from "./StatusBadge";
import { countEligibleChunks } from "./utils";
import type { ChunkLike, PageLike } from "./utils";

export interface ExtractionDocument {
  id: string;
  original_filename: string;
  processing_status: string;
  page_count: number | null;
}

/** Shown wherever OCR/canonical changes are possible once evidence links exist. */
export function EvidenceLockNotice() {
  return (
    <div className="x-banner x-banner-warn" role="note">
      Evidence links exist for this document. OCR reruns and canonical text changes are blocked to preserve existing
      evidence integrity.
    </div>
  );
}

export function ExtractionPanel({
  workspaceId,
  doc,
  pages,
  chunks,
  active,
  hasEvidence,
  onExtractionFinished,
}: {
  workspaceId: string;
  doc: ExtractionDocument | null;
  pages: PageLike[];
  chunks: ChunkLike[];
  active: boolean;
  hasEvidence: boolean;
  onExtractionFinished: () => void;
}) {
  const [running, setRunning] = useState(false);
  const [runningName, setRunningName] = useState("");
  const [runningDocId, setRunningDocId] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [run, setRun] = useState<{ run: ExtractionRun; httpStatus: number } | null>(null);
  const [error, setError] = useState<{ message: string; httpStatus: number | null } | null>(null);
  const [staleNotice, setStaleNotice] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  const lockRef = useRef(false); // synchronous guard: state updates are async
  const activeDocRef = useRef<string | null>(null);

  const docId = doc?.id ?? null;
  useEffect(() => {
    // New document selected: never show another document's run/error. Do not touch `running`.
    activeDocRef.current = docId;
    setRun(null);
    setError(null);
    setStaleNotice(null);
  }, [docId]);

  useEffect(() => {
    if (!running) return;
    const started = Date.now();
    setElapsed(0);
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [running]);

  const chunksById = useMemo(() => {
    const map: Record<string, ChunkPreview> = {};
    for (const c of chunks) map[c.id] = { chunk_index: c.chunk_index, page_start: c.page_start, content: c.content };
    return map;
  }, [chunks]);

  const eligible = useMemo(() => countEligibleChunks(pages, chunks), [pages, chunks]);
  const isReady = doc?.processing_status === "READY";
  const canExtract = doc !== null && isReady && eligible > 0 && !running;

  async function handleExtract() {
    if (lockRef.current || !doc || !isReady || eligible === 0) return;
    lockRef.current = true;
    const requestedDocId = doc.id;
    setRunning(true);
    setRunningName(doc.original_filename);
    setRunningDocId(requestedDocId);
    setRun(null);
    setError(null);
    setStaleNotice(null);
    try {
      // Exactly one POST; never retried automatically.
      const result = await extractDocument(workspaceId, requestedDocId);
      if (activeDocRef.current !== requestedDocId) {
        const outcome = result.kind === "run" ? `status ${String(result.run.status)}` : "an error";
        setStaleNotice(
          `An extraction request finished with ${outcome} for a document that is no longer selected. Select it again to view its facts and requirements.`,
        );
      } else if (result.kind === "run") {
        setRun({ run: result.run, httpStatus: result.httpStatus });
        setRefreshToken((n) => n + 1);
      } else {
        setError({ message: result.message, httpStatus: result.httpStatus });
        // Connection loss: the server may still have finished the run, so refresh the lists.
        if (result.httpStatus === null) setRefreshToken((n) => n + 1);
      }
      onExtractionFinished();
    } finally {
      lockRef.current = false;
      setRunning(false);
    }
  }

  if (!doc) {
    return (
      <div className="x-card x-empty">
        Select or upload a document in the sidebar to extract structured facts and requirements.
      </div>
    );
  }

  let readiness: { tone: "ok" | "warn" | "err"; text: string };
  if (!isReady) {
    readiness = {
      tone: "warn",
      text: `Document status is ${doc.processing_status}. Extraction requires a READY document — finish OCR / canonical selection first.`,
    };
  } else if (eligible === 0) {
    readiness = { tone: "warn", text: "This document is READY but has no eligible canonical chunks to extract from." };
  } else {
    readiness = { tone: "ok", text: "Ready for extraction." };
  }

  return (
    <div className="x-stack" hidden={!active}>
      <section className="x-card" aria-label="Structured extraction">
        <div className="x-card-head">
          <h3>Structured extraction</h3>
          <StatusBadge tone={isReady ? "ok" : "warn"}>{doc.processing_status}</StatusBadge>
        </div>
        <dl className="x-meta">
          <div>
            <dt>Document</dt>
            <dd>{doc.original_filename}</dd>
          </div>
          <div>
            <dt>Pages</dt>
            <dd>{doc.page_count ?? pages.length}</dd>
          </div>
          <div>
            <dt>Eligible canonical chunks</dt>
            <dd>{eligible}</dd>
          </div>
          <div>
            <dt>Workspace</dt>
            <dd>
              <code title={workspaceId}>{workspaceId.slice(0, 8)}…</code>
            </dd>
          </div>
        </dl>
        <div className={`x-banner x-banner-${readiness.tone}`} role="status">
          {readiness.text}
        </div>
        <p className="x-muted x-note">
          Eligible canonical chunks: {eligible}. One model call is made per eligible chunk. The server enforces the maximum model
          calls allowed per extraction request; oversized requests are rejected before model calls begin.
        </p>
        {hasEvidence && <EvidenceLockNotice />}

        <div className="x-actions">
          <button type="button" className="primary-button" onClick={() => void handleExtract()} disabled={!canExtract} aria-busy={running}>
            {running ? "Extracting…" : "Extract Facts & Requirements"}
          </button>
          <span className="x-muted x-note">
            Runs only when you click. Uses model credits. Re-running reuses identical proposals and keeps existing review states.
          </span>
        </div>

        {running && (
          <div className="x-banner x-banner-info" role="status">
            <Loading label={`Extracting “${runningName}”… ${elapsed}s elapsed`} />
            <div className="x-note">
              This may take several minutes. Leaving this page or losing the connection does not cancel the server-side run.
              {runningDocId !== doc.id && " This is a different document from the one selected now; wait for it to finish before starting another."}
            </div>
          </div>
        )}
        {error && (
          <div className="x-banner x-banner-err" role="alert">
            {error.httpStatus !== null && <strong>HTTP {error.httpStatus} · </strong>}
            {error.message}
          </div>
        )}
        {staleNotice && (
          <div className="x-banner x-banner-info" role="status">
            {staleNotice}
          </div>
        )}
      </section>

      {run && <RunSummary run={run.run} httpStatus={run.httpStatus} />}

      <FactsList
        key={`facts-${doc.id}`}
        workspaceId={workspaceId}
        documentId={doc.id}
        chunksById={chunksById}
        refreshToken={refreshToken}
        active={active}
      />
      <RequirementsList
        key={`reqs-${doc.id}`}
        workspaceId={workspaceId}
        documentId={doc.id}
        chunksById={chunksById}
        refreshToken={refreshToken}
        active={active}
      />
      <p className="x-muted x-note">
        Results are workspace-scoped. All results are model-proposed and not human reviewed; an exact quote match only shows the
        text exists in the source.
      </p>
    </div>
  );
}
