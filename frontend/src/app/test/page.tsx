"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

interface WorkspaceInfo {
  workspace_id: string;
  name: string;
  slug: string;
}

interface DocumentItem {
  id: string;
  original_filename: string;
  processing_status: string;
  file_size_bytes: number;
  page_count: number | null;
  sha256: string;
  created_at: string;
}

interface PageItem {
  id: string;
  page_number: number;
  extracted_text: string | null;
  extraction_method: string;
  extraction_status: string;
  page_metadata: Record<string, unknown>;
}

interface ChunkItem {
  id: string;
  chunk_index: number;
  page_start: number;
  page_end: number;
  content: string;
  token_count: number | null;
  content_hash: string;
}

interface OCRResult {
  page_number: number;
  char_count: number;
  extraction_method: string;
  extraction_status: string;
  languages_used: string;
  error_message: string | null;
}

interface OCRResponseData {
  document_id: string;
  processing_status: string;
  pages_processed: number;
  chunks_regenerated: number;
  results: OCRResult[];
  message: string;
}

export default function MilestoneTestPage() {
  const [workspace, setWorkspace] = useState<WorkspaceInfo | null>(null);
  const [activeTab, setActiveTab] = useState<"m1" | "m2" | "m3">("m2");
  const [loading, setLoading] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string>("");

  // Document list & selection
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [selectedDocId, setSelectedDocId] = useState<string>("");
  const [selectedDoc, setSelectedDoc] = useState<DocumentItem | null>(null);
  const [pages, setPages] = useState<PageItem[]>([]);
  const [chunks, setChunks] = useState<ChunkItem[]>([]);

  // OCR state
  const [ocrLangs, setOcrLangs] = useState("eng+mal");
  const [ocrDpi, setOcrDpi] = useState(300);
  const [ocrLoading, setOcrLoading] = useState(false);
  const [ocrResponse, setOcrResponse] = useState<OCRResponseData | null>(null);

  // Health & DB status
  const [healthLive, setHealthLive] = useState<string>("Checking...");
  const [healthReady, setHealthReady] = useState<string>("Checking...");

  // 1. Initial load: get health and default workspace
  async function init() {
    setLoading(true);
    try {
      const resLive = await fetch(`${API_BASE}/health/live`);
      setHealthLive(resLive.ok ? "OK (200)" : "FAIL");

      const resReady = await fetch(`${API_BASE}/health/ready`);
      setHealthReady(resReady.ok ? "Ready & PostgreSQL Connected (200)" : "Unready");

      const resWs = await fetch(`${API_BASE}/internal/dev/workspace-default`);
      if (resWs.ok) {
        const wsData: WorkspaceInfo = await resWs.json();
        setWorkspace(wsData);
        await loadDocuments(wsData.workspace_id);
      } else {
        setStatusMessage("Failed to fetch default workspace.");
      }
    } catch (e) {
      setStatusMessage(`Initialization failed: ${String(e)}`);
      setHealthLive("Offline");
      setHealthReady("Offline");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void init();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadDocuments(wsId: string) {
    try {
      const res = await fetch(`${API_BASE}/api/v1/workspaces/${wsId}/documents?limit=20`);
      if (res.ok) {
        const data = await res.json();
        setDocuments(data.items || []);
      }
    } catch (err) {
      console.error("Failed to load documents", err);
    }
  }

  async function selectDocument(docId: string) {
    if (!workspace) return;
    setSelectedDocId(docId);
    setLoading(true);
    setOcrResponse(null);

    try {
      const resDoc = await fetch(`${API_BASE}/api/v1/workspaces/${workspace.workspace_id}/documents/${docId}`);
      if (resDoc.ok) {
        const docData = await resDoc.json();
        setSelectedDoc(docData);
      }

      const resPages = await fetch(`${API_BASE}/api/v1/workspaces/${workspace.workspace_id}/documents/${docId}/pages`);
      if (resPages.ok) {
        const pagesData = await resPages.json();
        setPages(pagesData.items || []);
      }

      const resChunks = await fetch(`${API_BASE}/api/v1/workspaces/${workspace.workspace_id}/documents/${docId}/chunks`);
      if (resChunks.ok) {
        const chunksData = await resChunks.json();
        setChunks(chunksData.items || []);
      }
    } catch (err) {
      setStatusMessage(`Error fetching doc details: ${String(err)}`);
    } finally {
      setLoading(false);
    }
  }

  // Upload handler for custom file or sample
  async function handleFileUpload(file: File) {
    if (!workspace) return;
    setLoading(true);
    setStatusMessage(`Uploading ${file.name}...`);

    const formData = new FormData();
    formData.append("file", file, file.name);

    try {
      const res = await fetch(`${API_BASE}/api/v1/workspaces/${workspace.workspace_id}/documents`, {
        method: "POST",
        body: formData,
      });

      const resData = await res.json();
      if (res.ok) {
        setStatusMessage(`Upload successful! Status: ${resData.processing_status}`);
        await loadDocuments(workspace.workspace_id);
        const newId = resData.id || resData.existing_document_id;
        if (newId) {
          await selectDocument(newId);
        }
      } else {
        setStatusMessage(`Upload failed: ${resData.detail || "Unknown error"}`);
      }
    } catch (err) {
      setStatusMessage(`Upload error: ${String(err)}`);
    } finally {
      setLoading(false);
    }
  }

  // Quick helper to upload predefined test samples
  async function uploadSample(sampleName: "digital" | "scanned" | "malayalam") {
    let filename = "";
    if (sampleName === "digital") filename = "demo_digital_doc.pdf";
    if (sampleName === "scanned") filename = "demo_scanned_doc.pdf";
    if (sampleName === "malayalam") filename = "demo_malayalam_doc.pdf";

    try {
      setLoading(true);
      setStatusMessage(`Fetching test sample ${filename}...`);
      const resp = await fetch(`/samples/${filename}`);
      if (!resp.ok) {
        setStatusMessage(`Sample file /samples/${filename} not found in public folder.`);
        return;
      }
      const blob = await resp.blob();
      const file = new File([blob], filename, { type: "application/pdf" });
      await handleFileUpload(file);
    } catch (err) {
      setStatusMessage(`Failed to upload sample: ${String(err)}`);
    } finally {
      setLoading(false);
    }
  }

  // Run OCR on the selected document
  async function runOCR() {
    if (!workspace || !selectedDocId) return;
    setOcrLoading(true);
    setStatusMessage("Running OCR with PyMuPDF & Tesseract...");

    try {
      const res = await fetch(`${API_BASE}/api/v1/workspaces/${workspace.workspace_id}/documents/${selectedDocId}/ocr`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          languages: ocrLangs,
          dpi: ocrDpi,
        }),
      });

      const data = await res.json();
      if (res.ok) {
        setOcrResponse(data);
        setStatusMessage(`OCR Completed! Status: ${data.processing_status}, Chunks: ${data.chunks_regenerated}`);
        await selectDocument(selectedDocId);
      } else {
        setStatusMessage(`OCR Failed: ${data.detail || "Error occurred"}`);
      }
    } catch (err) {
      setStatusMessage(`OCR Request Error: ${String(err)}`);
    } finally {
      setOcrLoading(false);
    }
  }

  return (
    <main className="shell" style={{ paddingBottom: "60px" }}>
      {/* Header */}
      <nav className="nav">
        <Link className="brand" href="/" aria-label="Pramaan AI home">
          <span className="brand-mark">P</span>
          <span>pramaan<span className="brand-muted">.ai</span></span>
        </Link>
        <div style={{ display: "flex", gap: "12px", alignItems: "center" }}>
          <span style={{ fontSize: "12px", color: "var(--mint)" }}>🧪 Milestone Verification Console</span>
          <Link href="/" className="secondary-button" style={{ padding: "6px 12px", fontSize: "12px" }}>
            ← Home
          </Link>
        </div>
      </nav>

      {/* Title & Workspace Indicator */}
      <section style={{ marginTop: "30px", marginBottom: "20px" }}>
        <h1 style={{ fontSize: "32px", margin: "0 0 10px" }}>Interactive Milestone Test Suite</h1>
        <p style={{ color: "var(--muted)", margin: 0 }}>
          Verify and inspect database foundations, PDF ingestion, PyMuPDF extraction, deterministic chunking, and multilingual OCR.
        </p>

        {workspace && (
          <div style={{ marginTop: "14px", padding: "10px 14px", background: "rgba(140,240,200,0.06)", border: "1px solid rgba(140,240,200,0.2)", borderRadius: "8px", fontSize: "13px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span><strong>Active Workspace:</strong> {workspace.name} (<code>{workspace.workspace_id}</code>)</span>
            <button className="refresh-button" onClick={() => void init()}>Refresh All</button>
          </div>
        )}

        {statusMessage && (
          <div style={{ marginTop: "10px", padding: "8px 12px", background: "rgba(146, 183, 255, 0.1)", border: "1px solid #344356", borderRadius: "6px", fontSize: "13px", color: "var(--blue)" }}>
            ℹ️ {statusMessage}
          </div>
        )}
      </section>

      {/* Tabs */}
      <div style={{ display: "flex", gap: "10px", borderBottom: "1px solid var(--line)", paddingBottom: "10px", marginBottom: "24px" }}>
        <button
          className={activeTab === "m1" ? "primary-button" : "secondary-button"}
          style={{ padding: "8px 16px" }}
          onClick={() => setActiveTab("m1")}
        >
          Milestone 1: Database Foundation
        </button>
        <button
          className={activeTab === "m2" ? "primary-button" : "secondary-button"}
          style={{ padding: "8px 16px" }}
          onClick={() => setActiveTab("m2")}
        >
          Milestone 2: Ingestion &amp; Digital Docs
        </button>
        <button
          className={activeTab === "m3" ? "primary-button" : "secondary-button"}
          style={{ padding: "8px 16px" }}
          onClick={() => setActiveTab("m3")}
        >
          Milestone 3: Scanned &amp; Malayalam OCR
        </button>
      </div>

      {/* ─── TAB 1: MILESTONE 1 (DATABASE & HEALTH) ─── */}
      {activeTab === "m1" && (
        <section>
          <div className="feature-card" style={{ marginBottom: "20px" }}>
            <h2 style={{ marginTop: 0 }}>Milestone 1 Verification: Database Engine &amp; Migrations</h2>
            <p style={{ color: "var(--muted)", fontSize: "14px", marginBottom: "16px" }}>
              Verifies live connectivity to PostgreSQL 16 with pgvector, Alembic schema migrations, and transactional isolation.
            </p>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "15px" }}>
              <div style={{ background: "rgba(0,0,0,0.25)", padding: "14px", borderRadius: "8px", border: "1px solid var(--line)" }}>
                <span className="card-label">SERVICE LIVENESS</span>
                <div style={{ marginTop: "6px", fontSize: "16px", fontWeight: "bold", color: "var(--mint)" }}>{healthLive}</div>
                <div style={{ fontSize: "12px", color: "var(--muted)", marginTop: "4px" }}>Endpoint: <code>GET /health/live</code></div>
              </div>
              <div style={{ background: "rgba(0,0,0,0.25)", padding: "14px", borderRadius: "8px", border: "1px solid var(--line)" }}>
                <span className="card-label">DATABASE READINESS &amp; PGVECTOR</span>
                <div style={{ marginTop: "6px", fontSize: "16px", fontWeight: "bold", color: "var(--mint)" }}>{healthReady}</div>
                <div style={{ fontSize: "12px", color: "var(--muted)", marginTop: "4px" }}>Endpoint: <code>GET /health/ready</code></div>
              </div>
            </div>

            <div style={{ marginTop: "20px", background: "rgba(0,0,0,0.2)", padding: "14px", borderRadius: "8px", border: "1px solid var(--line)" }}>
              <h3 style={{ fontSize: "14px", margin: "0 0 8px" }}>Applied Schema Details:</h3>
              <ul style={{ fontSize: "13px", color: "var(--muted)", margin: 0, paddingLeft: "20px", lineHeight: "1.7" }}>
                <li><strong>20 Relational Tables:</strong> Workspaces, Users, Memberships, Documents, DocumentPages, DocumentChunks, ExtractedFacts, Requirements, GraphNodes, GraphEdges, Conflicts, Actions, Audits, ModelUsages, EvaluationRuns.</li>
                <li><strong>pgvector Vector(384):</strong> Multi-lingual embedding column with HNSW cosine similarity index.</li>
                <li><strong>Full Text Search:</strong> GIN tsvector index on <code>document_chunks.content</code>.</li>
                <li><strong>Alembic Revisions:</strong> <code>2421707795ab</code> (Initial baseline) + <code>fff3949058b2</code> (Milestone 2 status additions).</li>
              </ul>
            </div>
          </div>
        </section>
      )}

      {/* ─── TAB 2 & 3: DOCUMENT TESTING CONSOLE ─── */}
      {(activeTab === "m2" || activeTab === "m3") && (
        <section>
          {/* Quick upload presets banner */}
          <div style={{ background: "rgba(22,30,42,0.9)", border: "1px solid var(--line)", borderRadius: "12px", padding: "18px", marginBottom: "20px" }}>
            <h3 style={{ margin: "0 0 10px", fontSize: "16px" }}>⚡ Quick Test Predefined Documents:</h3>
            <p style={{ fontSize: "13px", color: "var(--muted)", margin: "0 0 14px" }}>
              Click any button below to instantly submit a generated test PDF into the backend ingestion engine:
            </p>
            <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
              <button
                className="secondary-button"
                style={{ background: activeTab === "m2" ? "rgba(140,240,200,0.12)" : "", borderColor: activeTab === "m2" ? "var(--mint)" : "" }}
                onClick={() => void uploadSample("digital")}
                disabled={loading}
              >
                📄 1. Test Digital Document (Milestone 2)
              </button>
              <button
                className="secondary-button"
                style={{ background: activeTab === "m3" ? "rgba(146,183,255,0.15)" : "", borderColor: activeTab === "m3" ? "var(--blue)" : "" }}
                onClick={() => void uploadSample("scanned")}
                disabled={loading}
              >
                🖼️ 2. Test Scanned Image PDF (Needs OCR)
              </button>
              <button
                className="secondary-button"
                style={{ background: activeTab === "m3" ? "rgba(146,183,255,0.15)" : "", borderColor: activeTab === "m3" ? "var(--blue)" : "" }}
                onClick={() => void uploadSample("malayalam")}
                disabled={loading}
              >
                🌐 3. Test Malayalam Gov Order (മലയാളം OCR)
              </button>
            </div>

            <div style={{ marginTop: "14px", paddingTop: "12px", borderTop: "1px solid var(--line)", display: "flex", alignItems: "center", gap: "12px" }}>
              <span style={{ fontSize: "13px", color: "var(--muted)" }}>Or upload your own local PDF:</span>
              <input
                type="file"
                accept="application/pdf"
                style={{ fontSize: "12px", color: "var(--muted)" }}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void handleFileUpload(f);
                }}
              />
            </div>
          </div>

          {/* Main workspace view: Documents column + Details column */}
          <div style={{ display: "grid", gridTemplateColumns: "320px 1fr", gap: "20px" }}>
            {/* Document list */}
            <div style={{ background: "rgba(17,23,32,0.85)", border: "1px solid var(--line)", borderRadius: "10px", padding: "14px", height: "fit-content", maxHeight: "680px", overflowY: "auto" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
                <span className="card-label">WORKSPACE DOCUMENTS</span>
                <span style={{ fontSize: "12px", color: "var(--muted)" }}>{documents.length} files</span>
              </div>

              {documents.length === 0 ? (
                <div style={{ padding: "20px", textAlign: "center", color: "var(--muted)", fontSize: "13px" }}>
                  No documents yet. Use the presets above to ingest your first test file!
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                  {documents.map((d) => (
                    <div
                      key={d.id}
                      onClick={() => void selectDocument(d.id)}
                      style={{
                        padding: "10px 12px",
                        borderRadius: "8px",
                        cursor: "pointer",
                        background: selectedDocId === d.id ? "rgba(140,240,200,0.1)" : "rgba(255,255,255,0.02)",
                        border: selectedDocId === d.id ? "1px solid var(--mint)" : "1px solid var(--line)",
                      }}
                    >
                      <div style={{ fontWeight: "600", fontSize: "13px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {d.original_filename}
                      </div>
                      <div style={{ display: "flex", justifyContent: "space-between", marginTop: "6px", fontSize: "11px" }}>
                        <span style={{
                          padding: "2px 6px",
                          borderRadius: "4px",
                          fontWeight: "bold",
                          background: d.processing_status === "READY" ? "rgba(140,240,200,0.15)" : d.processing_status === "NEEDS_OCR" ? "rgba(255,210,122,0.18)" : "rgba(255,137,137,0.15)",
                          color: d.processing_status === "READY" ? "var(--mint)" : d.processing_status === "NEEDS_OCR" ? "#ffd27a" : "#ff8989",
                        }}>
                          {d.processing_status}
                        </span>
                        <span style={{ color: "var(--muted)" }}>{d.page_count ?? 1} pg · {d.file_size_bytes} B</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Selected document details */}
            <div>
              {!selectedDoc ? (
                <div style={{ border: "1px dashed var(--line)", borderRadius: "10px", padding: "60px 20px", textAlign: "center", color: "var(--muted)" }}>
                  Select a document on the left or upload a sample above to view extracted text, chunks, and OCR controls.
                </div>
              ) : (
                <div>
                  {/* Metadata Header */}
                  <div style={{ background: "rgba(22,30,42,0.8)", border: "1px solid var(--line)", borderRadius: "10px", padding: "16px", marginBottom: "16px" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                      <div>
                        <h2 style={{ margin: "0 0 6px", fontSize: "18px" }}>{selectedDoc.original_filename}</h2>
                        <div style={{ fontSize: "12px", color: "var(--muted)", fontFamily: "monospace" }}>
                          ID: {selectedDoc.id} · SHA-256: {selectedDoc.sha256.substring(0, 16)}...
                        </div>
                      </div>
                      <span style={{
                        padding: "4px 10px",
                        borderRadius: "6px",
                        fontSize: "12px",
                        fontWeight: "bold",
                        background: selectedDoc.processing_status === "READY" ? "rgba(140,240,200,0.2)" : "rgba(255,210,122,0.2)",
                        color: selectedDoc.processing_status === "READY" ? "var(--mint)" : "#ffd27a",
                      }}>
                        {selectedDoc.processing_status}
                      </span>
                    </div>

                    {/* Milestone 3: OCR Action Box if document needs OCR */}
                    {(activeTab === "m3" || selectedDoc.processing_status === "NEEDS_OCR") && (
                      <div style={{ marginTop: "16px", padding: "12px 14px", background: "rgba(146,183,255,0.08)", border: "1px solid rgba(146,183,255,0.25)", borderRadius: "8px" }}>
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "10px" }}>
                          <div>
                            <strong style={{ fontSize: "13px", color: "var(--blue)" }}>🔍 Milestone 3 Optical Character Recognition (OCR):</strong>
                            <div style={{ fontSize: "12px", color: "var(--muted)", marginTop: "2px" }}>
                              Extract text from raster/scanned pages using PyMuPDF + Tesseract and regenerate deterministic chunks.
                            </div>
                          </div>
                          <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                            <label style={{ fontSize: "12px", color: "var(--muted)" }}>Langs:</label>
                            <input
                              type="text"
                              value={ocrLangs}
                              onChange={(e) => setOcrLangs(e.target.value)}
                              style={{ width: "90px", padding: "4px 6px", borderRadius: "4px", background: "#0b0e13", border: "1px solid var(--line)", color: "var(--text)", fontSize: "12px" }}
                            />
                            <label style={{ fontSize: "12px", color: "var(--muted)" }}>DPI:</label>
                            <input
                              type="number"
                              value={ocrDpi}
                              onChange={(e) => setOcrDpi(Number(e.target.value))}
                              style={{ width: "60px", padding: "4px 6px", borderRadius: "4px", background: "#0b0e13", border: "1px solid var(--line)", color: "var(--text)", fontSize: "12px" }}
                            />
                            <button
                              className="primary-button"
                              style={{ padding: "6px 14px", fontSize: "12px" }}
                              onClick={() => void runOCR()}
                              disabled={ocrLoading}
                            >
                              {ocrLoading ? "Running OCR..." : "⚡ Trigger OCR"}
                            </button>
                          </div>
                        </div>

                        {ocrResponse && (
                          <div style={{ marginTop: "10px", padding: "8px", background: "rgba(0,0,0,0.3)", borderRadius: "6px", fontSize: "12px" }}>
                            ✅ <strong>OCR Result:</strong> {ocrResponse.message} · Status: <code>{ocrResponse.processing_status}</code> · Pages: {ocrResponse.pages_processed} · Regenerated Chunks: {ocrResponse.chunks_regenerated}
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Extracted Pages view */}
                  <div style={{ marginBottom: "20px" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                      <span className="card-label">EXTRACTED PAGES ({pages.length})</span>
                      <span style={{ fontSize: "12px", color: "var(--muted)" }}>1-based Page Provenance</span>
                    </div>

                    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                      {pages.map((p) => (
                        <div key={p.id} style={{ background: "rgba(22,30,42,0.6)", border: "1px solid var(--line)", borderRadius: "8px", padding: "14px" }}>
                          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "8px", fontSize: "12px" }}>
                            <span><strong>Page {p.page_number}</strong> · Method: <code style={{ color: "var(--blue)" }}>{p.extraction_method}</code></span>
                            <span style={{
                              padding: "2px 6px",
                              borderRadius: "4px",
                              fontSize: "11px",
                              fontWeight: "bold",
                              background: p.extraction_status === "COMPLETED" ? "rgba(140,240,200,0.15)" : "rgba(255,210,122,0.15)",
                              color: p.extraction_status === "COMPLETED" ? "var(--mint)" : "#ffd27a",
                            }}>
                              {p.extraction_status}
                            </span>
                          </div>
                          <div style={{
                            background: "#090d12",
                            border: "1px solid #1a222d",
                            borderRadius: "6px",
                            padding: "10px 12px",
                            fontFamily: "monospace",
                            fontSize: "13px",
                            lineHeight: "1.6",
                            maxHeight: "180px",
                            overflowY: "auto",
                            whiteSpace: "pre-wrap",
                            color: p.extracted_text ? "var(--text)" : "var(--muted)",
                          }}>
                            {p.extracted_text || "(No digital text detected — page requires OCR)"}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Chunks View */}
                  <div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                      <span className="card-label">DETERMINISTIC CHUNKS ({chunks.length})</span>
                      <span style={{ fontSize: "12px", color: "var(--muted)" }}>Ordered Character Windows</span>
                    </div>

                    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                      {chunks.length === 0 ? (
                        <div style={{ padding: "16px", border: "1px dashed var(--line)", borderRadius: "8px", color: "var(--muted)", fontSize: "13px" }}>
                          No chunks generated yet (page has insufficient text or is waiting for OCR).
                        </div>
                      ) : (
                        chunks.map((c) => (
                          <div key={c.id} style={{ background: "rgba(17,23,32,0.6)", border: "1px solid var(--line)", borderRadius: "8px", padding: "12px" }}>
                            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "11px", color: "var(--muted)", marginBottom: "6px" }}>
                              <span><strong>Chunk #{c.chunk_index}</strong> · Pages {c.page_start}–{c.page_end} · Tokens: ~{c.token_count ?? 0}</span>
                              <span style={{ fontFamily: "monospace" }}>Hash: {c.content_hash.substring(0, 14)}...</span>
                            </div>
                            <div style={{
                              background: "#080c10",
                              borderRadius: "4px",
                              padding: "8px 10px",
                              fontSize: "12px",
                              lineHeight: "1.5",
                              color: "#d0d8e2",
                              whiteSpace: "pre-wrap",
                            }}>
                              {c.content}
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </section>
      )}
    </main>
  );
}
