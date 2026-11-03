"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { EvidenceLockNotice, ExtractionPanel } from "@/components/extraction/ExtractionPanel";
import { listFacts, listRequirements } from "@/lib/api/extraction";

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

interface PageExtractionAttempt {
  id: string;
  page_number: number;
  provider: string;
  model_id: string | null;
  prompt_version: string | null;
  language_configuration: string | null;
  extracted_text: string;
  status: string;
  latency_ms: number | null;
  usage_metadata: Record<string, unknown>;
  error_code: string | null;
  created_at: string;
}

interface PageComparison {
  page_number: number;
  canonical_method: string;
  canonical_text: string;
  tesseract_attempt: PageExtractionAttempt | null;
  meta_vision_attempt: PageExtractionAttempt | null;
}

interface OCRResult {
  page_number: number;
  char_count: number;
  extraction_method: string;
  extraction_status: string;
  languages_used: string;
  provider: string;
  error_message: string | null;
  uncertain_regions?: Array<{ text_excerpt: string; reason: string }>;
  latency_ms?: number | null;
}

interface OCRResponseData {
  document_id: string;
  processing_status: string;
  provider_mode: string;
  pages_processed: number;
  chunks_regenerated: number;
  results: OCRResult[];
  comparisons: PageComparison[];
  message: string;
}

export default function MilestoneTestPage() {
  const [workspace, setWorkspace] = useState<WorkspaceInfo | null>(null);
  const [activeTab, setActiveTab] = useState<"m1" | "m2" | "m3" | "m31" | "m4">("m31");
  const [loading, setLoading] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string>("");

  // Document list & selection
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [selectedDocId, setSelectedDocId] = useState<string>("");
  const [selectedDoc, setSelectedDoc] = useState<DocumentItem | null>(null);
  const [pages, setPages] = useState<PageItem[]>([]);
  const [chunks, setChunks] = useState<ChunkItem[]>([]);
  const [attempts, setAttempts] = useState<PageExtractionAttempt[]>([]);
  const [comparisons, setComparisons] = useState<PageComparison[]>([]);

  // OCR state
  const [targetPage, setTargetPage] = useState<string>("all");
  const [ocrProvider, setOcrProvider] = useState<"tesseract" | "meta_vision" | "compare">("meta_vision");
  const [fallbackToTesseract, setFallbackToTesseract] = useState(true);
  const [ocrLangs, setOcrLangs] = useState("eng+mal");
  const [ocrDpi, setOcrDpi] = useState(250);
  const [ocrLoading, setOcrLoading] = useState(false);
  const [ocrResponse, setOcrResponse] = useState<OCRResponseData | null>(null);

  // Structured extraction (Milestone 4): whether the selected document already has evidence links.
  // Evidence links block OCR reruns / canonical changes on the backend (HTTP 409); this only informs the user.
  const [hasEvidence, setHasEvidence] = useState(false);
  const [evidenceProbe, setEvidenceProbe] = useState(0);
  const probeSeq = useRef(0);

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

  // Read-only probe (GET only, never extracts): does the selected document have evidence-linked results?
  useEffect(() => {
    const seq = ++probeSeq.current;
    if (!workspace || !selectedDocId) {
      setHasEvidence(false);
      return;
    }
    void Promise.all([
      listFacts(workspace.workspace_id, selectedDocId, { limit: 1 }),
      listRequirements(workspace.workspace_id, selectedDocId, { limit: 1 }),
    ]).then(([facts, reqs]) => {
      if (seq !== probeSeq.current) return; // stale: another document was selected meanwhile
      const factEvidence = facts.ok && facts.data.items.some((f) => f.evidence.length > 0);
      const reqEvidence = reqs.ok && reqs.data.items.some((r) => r.evidence.length > 0);
      setHasEvidence(factEvidence || reqEvidence);
    });
  }, [workspace, selectedDocId, evidenceProbe]);

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

      // Fetch extraction attempts
      const resAttempts = await fetch(`${API_BASE}/api/v1/workspaces/${workspace.workspace_id}/documents/${docId}/extraction-attempts`);
      if (resAttempts.ok) {
        const attData = await resAttempts.json();
        setAttempts(attData || []);
      }

      // Fetch comparisons
      const resComp = await fetch(`${API_BASE}/api/v1/workspaces/${workspace.workspace_id}/documents/${docId}/compare`);
      if (resComp.ok) {
        const compData = await resComp.json();
        setComparisons(compData || []);
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

  // Run OCR / Vision on the selected document
  async function runOCR(overrideProvider?: "tesseract" | "meta_vision" | "compare") {
    if (!workspace || !selectedDocId) return;
    const providerToUse = overrideProvider || ocrProvider;
    setOcrLoading(true);
    setStatusMessage(`Processing pages via provider: ${providerToUse.toUpperCase()}...`);

    const pageNums = targetPage === "all" ? pages.map((p) => p.page_number) : [Number(targetPage)];

    try {
      const res = await fetch(`${API_BASE}/api/v1/workspaces/${workspace.workspace_id}/documents/${selectedDocId}/ocr`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: providerToUse,
          fallback_to_tesseract: fallbackToTesseract,
          languages: ocrLangs,
          dpi: ocrDpi,
          page_numbers: pageNums.length > 0 ? pageNums : undefined,
        }),
      });

      const data = await res.json();
      if (res.ok) {
        setOcrResponse(data);
        if (data.comparisons && data.comparisons.length > 0) {
          setComparisons(data.comparisons);
        }
        setStatusMessage(`Extraction Completed via ${providerToUse.toUpperCase()}! Processed ${data.pages_processed} page(s).`);
        await selectDocument(selectedDocId);
      } else {
        setStatusMessage(`Extraction Failed: ${data.detail || "Error occurred"}`);
      }
    } catch (err) {
      setStatusMessage(`Request Error: ${String(err)}`);
    } finally {
      setOcrLoading(false);
    }
  }

  // Select Canonical Extraction (Milestone 3.1.1)
  async function selectCanonical(pageNum: number | null, provider: string) {
    if (!workspace || !selectedDocId) return;
    setLoading(true);
    setStatusMessage(`Setting canonical provider to ${provider.toUpperCase()}${pageNum ? ` for page ${pageNum}` : " for all pages"}...`);

    try {
      const url = pageNum
        ? `${API_BASE}/api/v1/workspaces/${workspace.workspace_id}/documents/${selectedDocId}/pages/${pageNum}/canonical`
        : `${API_BASE}/api/v1/workspaces/${workspace.workspace_id}/documents/${selectedDocId}/canonical`;

      const res = await fetch(url, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider }),
      });

      const data = await res.json();
      if (res.ok) {
        setStatusMessage(`Canonical updated! ${data.message} · Chunks regenerated: ${data.chunks_regenerated}`);
        await selectDocument(selectedDocId);
      } else {
        setStatusMessage(`Failed to set canonical: ${data.detail || "Error"}`);
      }
    } catch (err) {
      setStatusMessage(`Request error: ${String(err)}`);
    } finally {
      setLoading(false);
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
          Verify and inspect database foundations, PDF ingestion, PyMuPDF extraction, deterministic chunking, Tesseract OCR, and Meta Muse Spark Vision.
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
      <div style={{ display: "flex", gap: "10px", borderBottom: "1px solid var(--line)", paddingBottom: "10px", marginBottom: "24px", overflowX: "auto" }}>
        <button
          className={activeTab === "m1" ? "primary-button" : "secondary-button"}
          style={{ padding: "8px 14px", fontSize: "13px", whiteSpace: "nowrap" }}
          onClick={() => setActiveTab("m1")}
        >
          Milestone 1: Database Foundation
        </button>
        <button
          className={activeTab === "m2" ? "primary-button" : "secondary-button"}
          style={{ padding: "8px 14px", fontSize: "13px", whiteSpace: "nowrap" }}
          onClick={() => setActiveTab("m2")}
        >
          Milestone 2: Ingestion &amp; Digital Docs
        </button>
        <button
          className={activeTab === "m3" ? "primary-button" : "secondary-button"}
          style={{ padding: "8px 14px", fontSize: "13px", whiteSpace: "nowrap" }}
          onClick={() => setActiveTab("m3")}
        >
          Milestone 3: Tesseract OCR (Local)
        </button>
        <button
          className={activeTab === "m31" ? "primary-button" : "secondary-button"}
          style={{ padding: "8px 14px", fontSize: "13px", whiteSpace: "nowrap", border: activeTab === "m31" ? "1px solid var(--mint)" : "1px solid #4a6fa5" }}
          onClick={() => setActiveTab("m31")}
        >
          ✨ Milestone 3.1: Meta Muse Spark Vision
        </button>
        <button
          className={activeTab === "m4" ? "primary-button" : "secondary-button"}
          style={{ padding: "8px 14px", fontSize: "13px", whiteSpace: "nowrap", border: activeTab === "m4" ? "1px solid var(--mint)" : "1px solid #4a6fa5" }}
          onClick={() => setActiveTab("m4")}
        >
          Milestone 4: Structured Extraction
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
          </div>
        </section>
      )}

      {/* ─── TABS 2, 3, 3.1: INGESTION, OCR & META VISION ─── */}
      {activeTab !== "m1" && (
        <section>
          <div className="test-layout">
            {/* Sidebar: Upload & Document Selector */}
            <div>
              <div className="feature-card" style={{ marginBottom: "20px" }}>
                <h3 style={{ margin: "0 0 10px", fontSize: "16px" }}>1. Upload Document</h3>
                <p style={{ fontSize: "12px", color: "var(--muted)", marginBottom: "12px" }}>
                  Upload a PDF to verify ingestion, extraction, or OCR processing.
                </p>

                {/* Pre-made quick test sample buttons */}
                <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginBottom: "14px" }}>
                  <button
                    className="secondary-button"
                    style={{ fontSize: "12px", padding: "8px", textAlign: "left", display: "flex", justifyContent: "space-between" }}
                    onClick={() => void uploadSample("digital")}
                    disabled={loading}
                  >
                    <span>📄 Demo Digital Policy</span>
                    <span style={{ color: "var(--mint)", fontSize: "11px" }}>READY</span>
                  </button>

                  <button
                    className="secondary-button"
                    style={{ fontSize: "12px", padding: "8px", textAlign: "left", display: "flex", justifyContent: "space-between" }}
                    onClick={() => void uploadSample("scanned")}
                    disabled={loading}
                  >
                    <span>🖼️ Demo Scanned Notice</span>
                    <span style={{ color: "#ffd27a", fontSize: "11px" }}>NEEDS_OCR</span>
                  </button>

                  <button
                    className="secondary-button"
                    style={{ fontSize: "12px", padding: "8px", textAlign: "left", display: "flex", justifyContent: "space-between" }}
                    onClick={() => void uploadSample("malayalam")}
                    disabled={loading}
                  >
                    <span>📜 Demo Malayalam Circular</span>
                    <span style={{ color: "#92b7ff", fontSize: "11px" }}>MULTILINGUAL</span>
                  </button>
                </div>

                <div style={{ borderTop: "1px solid var(--line)", paddingTop: "12px" }}>
                  <label style={{ fontSize: "12px", color: "var(--muted)", display: "block", marginBottom: "6px" }}>
                    Or Upload Custom PDF:
                  </label>
                  <input
                    type="file"
                    accept="application/pdf"
                    onChange={(e) => {
                      if (e.target.files && e.target.files[0]) {
                        void handleFileUpload(e.target.files[0]);
                      }
                    }}
                    style={{ fontSize: "12px", width: "100%" }}
                  />
                </div>
              </div>

              {/* Document List */}
              <div className="feature-card">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
                  <h3 style={{ margin: 0, fontSize: "16px" }}>Documents ({documents.length})</h3>
                  <button
                    className="refresh-button"
                    style={{ fontSize: "11px" }}
                    onClick={() => workspace && void loadDocuments(workspace.workspace_id)}
                  >
                    Refresh
                  </button>
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: "6px", maxHeight: "400px", overflowY: "auto" }}>
                  {documents.length === 0 ? (
                    <div style={{ fontSize: "12px", color: "var(--muted)", padding: "10px 0" }}>
                      No documents yet in this workspace. Upload a sample above.
                    </div>
                  ) : (
                    documents.map((doc) => {
                      const isSelected = doc.id === selectedDocId;
                      return (
                        <div
                          key={doc.id}
                          onClick={() => void selectDocument(doc.id)}
                          style={{
                            padding: "8px 10px",
                            borderRadius: "6px",
                            background: isSelected ? "rgba(140,240,200,0.12)" : "rgba(255,255,255,0.03)",
                            border: isSelected ? "1px solid var(--mint)" : "1px solid var(--line)",
                            cursor: "pointer",
                            fontSize: "12px",
                          }}
                        >
                          <div style={{ fontWeight: "bold", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                            {doc.original_filename}
                          </div>
                          <div style={{ display: "flex", justifyContent: "space-between", marginTop: "4px", color: "var(--muted)", fontSize: "11px" }}>
                            <span>{doc.page_count ?? 1} pg · {(doc.file_size_bytes / 1024).toFixed(1)} KB</span>
                            <span style={{
                              fontWeight: "bold",
                              color: doc.processing_status === "READY" ? "var(--mint)" : doc.processing_status === "NEEDS_OCR" ? "#ffd27a" : "var(--coral)",
                            }}>
                              {doc.processing_status}
                            </span>
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>
            </div>

            {/* Main Area: Document Inspector & OCR Controls */}
            <div>
              {!selectedDoc ? (
                <div className="feature-card" style={{ textAlign: "center", padding: "60px 20px", color: "var(--muted)" }}>
                  👈 Select or upload a document on the left to inspect extraction, provenance, or trigger OCR.
                </div>
              ) : (
                <div>
                  {/* Document Header */}
                  <div className="feature-card" style={{ marginBottom: "20px" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "10px" }}>
                      <div>
                        <h2 style={{ margin: "0 0 6px", fontSize: "20px" }}>{selectedDoc.original_filename}</h2>
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

                    {activeTab !== "m4" && hasEvidence && (
                      <div style={{ marginTop: "16px" }}>
                        <EvidenceLockNotice />
                      </div>
                    )}

                    {/* Extraction Control Box (Milestone 3 & 3.1) — hidden on the structured-extraction tab */}
                    {activeTab !== "m4" && (
                    <div style={{ marginTop: "16px", padding: "14px", background: "rgba(146,183,255,0.06)", border: "1px solid rgba(146,183,255,0.25)", borderRadius: "8px" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "10px" }}>
                        <div>
                          <strong style={{ fontSize: "14px", color: "var(--blue)" }}>
                            {activeTab === "m31" ? "✨ Milestone 3.1 Vision & OCR Engine:" : "🔍 Milestone 3 Optical Character Recognition:"}
                          </strong>
                          <div style={{ fontSize: "12px", color: "var(--muted)", marginTop: "2px" }}>
                            {activeTab === "m31"
                              ? "Transcribe scanned operational pages via Meta Muse Spark 1.3 Vision, Tesseract local OCR, or dual Compare mode."
                              : "Extract text from raster/scanned pages using PyMuPDF + Tesseract and regenerate deterministic chunks."}
                          </div>
                        </div>

                        {/* Controls */}
                        <div style={{ display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" }}>
                          {activeTab === "m31" && (
                            <>
                              <label style={{ fontSize: "12px", color: "var(--muted)" }}>Provider:</label>
                              <select
                                value={ocrProvider}
                                onChange={(e) => setOcrProvider(e.target.value as "tesseract" | "meta_vision" | "compare")}
                                style={{ padding: "4px 8px", borderRadius: "4px", background: "#0b0e13", border: "1px solid var(--line)", color: "var(--text)", fontSize: "12px" }}
                              >
                                <option value="meta_vision">Meta Muse Spark 1.3 Vision</option>
                                <option value="compare">Compare Mode (Both)</option>
                                <option value="tesseract">Tesseract (Local)</option>
                              </select>

                              <label style={{ fontSize: "12px", color: "var(--muted)" }}>Pages:</label>
                              <select
                                value={targetPage}
                                onChange={(e) => setTargetPage(e.target.value)}
                                style={{ padding: "4px 8px", borderRadius: "4px", background: "#0b0e13", border: "1px solid var(--line)", color: "var(--text)", fontSize: "12px" }}
                              >
                                <option value="all">All Pages ({pages.length})</option>
                                {pages.map((p) => (
                                  <option key={p.page_number} value={p.page_number}>
                                    Page {p.page_number}
                                  </option>
                                ))}
                              </select>

                              {ocrProvider === "meta_vision" && (
                                <label style={{ fontSize: "11px", color: "var(--muted)", display: "flex", alignItems: "center", gap: "4px" }}>
                                  <input
                                    type="checkbox"
                                    checked={fallbackToTesseract}
                                    onChange={(e) => setFallbackToTesseract(e.target.checked)}
                                  />
                                  Fallback to Tesseract
                                </label>
                              )}
                            </>
                          )}

                          {(activeTab === "m3" || ocrProvider === "tesseract") && (
                            <>
                              <label style={{ fontSize: "12px", color: "var(--muted)" }}>Langs:</label>
                              <input
                                type="text"
                                value={ocrLangs}
                                onChange={(e) => setOcrLangs(e.target.value)}
                                style={{ width: "80px", padding: "4px 6px", borderRadius: "4px", background: "#0b0e13", border: "1px solid var(--line)", color: "var(--text)", fontSize: "12px" }}
                              />
                            </>
                          )}

                          <label style={{ fontSize: "12px", color: "var(--muted)" }}>DPI:</label>
                          <input
                            type="number"
                            value={ocrDpi}
                            onChange={(e) => setOcrDpi(Number(e.target.value))}
                            style={{ width: "55px", padding: "4px 6px", borderRadius: "4px", background: "#0b0e13", border: "1px solid var(--line)", color: "var(--text)", fontSize: "12px" }}
                          />

                          <button
                            className="primary-button"
                            style={{ padding: "6px 14px", fontSize: "12px" }}
                            onClick={() => void runOCR()}
                            disabled={ocrLoading}
                          >
                            {ocrLoading ? "Processing..." : activeTab === "m31" ? "⚡ Transcribe Page" : "⚡ Trigger OCR"}
                          </button>
                        </div>
                      </div>

                      {ocrResponse && (
                        <div style={{ marginTop: "12px", padding: "10px", background: "rgba(0,0,0,0.35)", borderRadius: "6px", fontSize: "12px", display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "8px" }}>
                          <div>
                            ✅ <strong>Result:</strong> {ocrResponse.message} · Mode: <code style={{ color: "var(--mint)" }}>{ocrResponse.provider_mode}</code> · Status: <code>{ocrResponse.processing_status}</code> · Pages: {ocrResponse.pages_processed} · Regenerated Chunks: {ocrResponse.chunks_regenerated}
                          </div>
                          {ocrResponse.results[0]?.latency_ms && (
                            <span style={{ fontSize: "11px", color: "var(--muted)" }}>
                              ⏱️ Latency: {ocrResponse.results[0].latency_ms} ms
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                    )}
                  </div>

                  {/* Structured extraction dashboard (Milestone 4). Kept mounted so an in-flight run survives tab switches. */}
                  <ExtractionPanel
                    workspaceId={workspace?.workspace_id ?? ""}
                    doc={selectedDoc}
                    pages={pages}
                    chunks={chunks}
                    active={activeTab === "m4"}
                    hasEvidence={hasEvidence}
                    onExtractionFinished={() => setEvidenceProbe((n) => n + 1)}
                  />

                  {activeTab !== "m4" && (
                  <>
                  {/* Dual Comparison View (when compare mode is active or comparisons exist) */}
                  {comparisons.length > 0 && (
                    <div style={{ marginBottom: "20px" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px", flexWrap: "wrap", gap: "8px" }}>
                        <span className="card-label">⚡ DUAL PROVIDER COMPARISON (TESSERACT VS META VISION)</span>
                        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                          <span style={{ fontSize: "11px", color: "var(--muted)" }}>Document-wide Canonical:</span>
                          <button
                            className="secondary-button"
                            style={{ padding: "2px 8px", fontSize: "11px", color: "var(--mint)", borderColor: "rgba(140,240,200,0.3)" }}
                            onClick={() => void selectCanonical(null, "meta_vision")}
                            disabled={loading}
                          >
                            ★ Set All to Muse Spark
                          </button>
                          <button
                            className="secondary-button"
                            style={{ padding: "2px 8px", fontSize: "11px", color: "var(--blue)", borderColor: "rgba(146,183,255,0.3)" }}
                            onClick={() => void selectCanonical(null, "tesseract")}
                            disabled={loading}
                          >
                            ★ Set All to Tesseract
                          </button>
                        </div>
                      </div>

                      <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                        {comparisons.map((c) => {
                          const isTessCanonical = c.canonical_method.toUpperCase() === "OCR" || c.canonical_method.toUpperCase() === "TESSERACT";
                          const isVisionCanonical = c.canonical_method.toUpperCase() === "META_VISION";
                          return (
                            <div key={c.page_number} style={{ background: "rgba(22,30,42,0.7)", border: "1px solid var(--line)", borderRadius: "8px", padding: "14px" }}>
                              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "10px", fontSize: "12px", alignItems: "center" }}>
                                <span>
                                  <strong>Page {c.page_number}</strong> · Authoritative Canonical Method: <code style={{ color: isVisionCanonical ? "var(--mint)" : isTessCanonical ? "var(--blue)" : "var(--text)" }}>{c.canonical_method}</code>
                                </span>
                                <span style={{ color: "var(--muted)", fontSize: "11px" }}>
                                  Protected during compare runs
                                </span>
                              </div>

                              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                                {/* Left: Tesseract Attempt */}
                                <div style={{ background: "#0b0f14", border: isTessCanonical ? "1px solid rgba(146,183,255,0.6)" : "1px solid #1f2a38", borderRadius: "6px", padding: "10px" }}>
                                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
                                    <div>
                                      <strong style={{ fontSize: "11px", color: "var(--blue)" }}>TESSERACT OCR (LOCAL)</strong>
                                      {isTessCanonical && (
                                        <span style={{ marginLeft: "6px", padding: "1px 5px", borderRadius: "3px", fontSize: "9px", fontWeight: "bold", background: "rgba(146,183,255,0.2)", color: "var(--blue)" }}>
                                          ★ CANONICAL
                                        </span>
                                      )}
                                    </div>
                                    <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
                                      <span style={{ fontSize: "11px", color: "var(--muted)" }}>{c.tesseract_attempt ? `${c.tesseract_attempt.extracted_text.length} chars` : "Not run"}</span>
                                      {c.tesseract_attempt && !isTessCanonical && (
                                        <button
                                          className="secondary-button"
                                          style={{ padding: "2px 6px", fontSize: "10px", borderColor: "var(--blue)", color: "var(--blue)" }}
                                          onClick={() => void selectCanonical(c.page_number, "tesseract")}
                                          disabled={loading}
                                        >
                                          Set as Canonical
                                        </button>
                                      )}
                                    </div>
                                  </div>
                                  <div style={{ fontFamily: "monospace", fontSize: "12px", lineHeight: "1.5", maxHeight: "150px", overflowY: "auto", whiteSpace: "pre-wrap", color: "#b8c5d6" }}>
                                    {c.tesseract_attempt?.extracted_text || "(No Tesseract attempt recorded for this page)"}
                                  </div>
                                </div>

                                {/* Right: Meta Vision Attempt */}
                                <div style={{ background: "#0b0f14", border: isVisionCanonical ? "1px solid rgba(140,240,200,0.6)" : "1px solid #28374a", borderRadius: "6px", padding: "10px" }}>
                                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
                                    <div>
                                      <strong style={{ fontSize: "11px", color: "var(--mint)" }}>META MUSE SPARK 1.3 VISION</strong>
                                      {isVisionCanonical && (
                                        <span style={{ marginLeft: "6px", padding: "1px 5px", borderRadius: "3px", fontSize: "9px", fontWeight: "bold", background: "rgba(140,240,200,0.2)", color: "var(--mint)" }}>
                                          ★ CANONICAL
                                        </span>
                                      )}
                                    </div>
                                    <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
                                      <span style={{ fontSize: "11px", color: "var(--muted)" }}>
                                        {c.meta_vision_attempt
                                          ? `${c.meta_vision_attempt.extracted_text.length} chars · ${c.meta_vision_attempt.latency_ms ?? 0} ms`
                                          : "Not run"}
                                      </span>
                                      {c.meta_vision_attempt && !isVisionCanonical && (
                                        <button
                                          className="primary-button"
                                          style={{ padding: "2px 6px", fontSize: "10px", borderColor: "var(--mint)", color: "var(--mint)", background: "rgba(140,240,200,0.15)" }}
                                          onClick={() => void selectCanonical(c.page_number, "meta_vision")}
                                          disabled={loading}
                                        >
                                          Set as Canonical
                                        </button>
                                      )}
                                    </div>
                                  </div>
                                  <div style={{ fontFamily: "monospace", fontSize: "12px", lineHeight: "1.5", maxHeight: "150px", overflowY: "auto", whiteSpace: "pre-wrap", color: "#d2e2f3" }}>
                                    {c.meta_vision_attempt?.extracted_text || "(No Meta Vision attempt recorded for this page)"}
                                  </div>
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* Extraction Attempts Audit Log (Milestone 3.1 Provenance) */}
                  {attempts.length > 0 && (
                    <div style={{ marginBottom: "20px" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                        <span className="card-label">PROVENANCE: PAGE EXTRACTION ATTEMPTS ({attempts.length})</span>
                        <span style={{ fontSize: "12px", color: "var(--muted)" }}>Audit table: <code>page_extraction_attempts</code></span>
                      </div>

                      <div style={{ overflowX: "auto" }}>
                        <table style={{ width: "100%", fontSize: "12px", borderCollapse: "collapse", background: "rgba(15,20,28,0.6)", borderRadius: "8px" }}>
                          <thead>
                            <tr style={{ borderBottom: "1px solid var(--line)", textAlign: "left", color: "var(--muted)" }}>
                              <th style={{ padding: "8px 10px" }}>Pg</th>
                              <th style={{ padding: "8px 10px" }}>Provider</th>
                              <th style={{ padding: "8px 10px" }}>Model</th>
                              <th style={{ padding: "8px 10px" }}>Prompt Ver</th>
                              <th style={{ padding: "8px 10px" }}>Status</th>
                              <th style={{ padding: "8px 10px" }}>Latency</th>
                              <th style={{ padding: "8px 10px" }}>Length</th>
                              <th style={{ padding: "8px 10px" }}>Created At</th>
                            </tr>
                          </thead>
                          <tbody>
                            {attempts.map((att) => (
                              <tr key={att.id} style={{ borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
                                <td style={{ padding: "8px 10px" }}>Page {att.page_number}</td>
                                <td style={{ padding: "8px 10px" }}>
                                  <code style={{ color: att.provider === "meta_vision" ? "var(--mint)" : "var(--blue)" }}>
                                    {att.provider}
                                  </code>
                                </td>
                                <td style={{ padding: "8px 10px", color: "var(--muted)" }}>{att.model_id || "tesseract-5.x"}</td>
                                <td style={{ padding: "8px 10px", color: "var(--muted)" }}>{att.prompt_version || "—"}</td>
                                <td style={{ padding: "8px 10px" }}>
                                  <span style={{
                                    padding: "2px 6px",
                                    borderRadius: "4px",
                                    fontSize: "10px",
                                    fontWeight: "bold",
                                    background: att.status === "COMPLETED" ? "rgba(140,240,200,0.15)" : "rgba(255,100,100,0.15)",
                                    color: att.status === "COMPLETED" ? "var(--mint)" : "var(--coral)",
                                  }}>
                                    {att.status}
                                  </span>
                                </td>
                                <td style={{ padding: "8px 10px", color: "var(--muted)" }}>{att.latency_ms ? `${att.latency_ms} ms` : "—"}</td>
                                <td style={{ padding: "8px 10px" }}>{att.extracted_text.length} chars</td>
                                <td style={{ padding: "8px 10px", color: "var(--muted)", fontSize: "11px" }}>
                                  {new Date(att.created_at).toLocaleTimeString()}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* Extracted Pages view */}
                  <div style={{ marginBottom: "20px" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                      <span className="card-label">CURRENT EXTRACTED PAGES ({pages.length})</span>
                      <span style={{ fontSize: "12px", color: "var(--muted)" }}>Canonical Page Text in DB</span>
                    </div>

                    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                      {pages.map((p) => {
                        const uncertainList = (p.page_metadata?.uncertain_regions as Array<{ text_excerpt: string; reason: string }>) || [];
                        return (
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

                            {/* Uncertain Regions Banner if present */}
                            {uncertainList.length > 0 && (
                              <div style={{ marginBottom: "8px", padding: "6px 10px", background: "rgba(255,210,122,0.1)", border: "1px solid rgba(255,210,122,0.3)", borderRadius: "4px", fontSize: "11px", color: "#ffd27a" }}>
                                ⚠️ <strong>Uncertain Passages Flagged ({uncertainList.length}):</strong>{" "}
                                {uncertainList.map((u, i) => (
                                  <span key={i} style={{ marginRight: "10px" }}>
                                    &ldquo;{u.text_excerpt}&rdquo; ({u.reason})
                                  </span>
                                ))}
                              </div>
                            )}

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
                        );
                      })}
                    </div>
                  </div>

                  {/* Chunks View */}
                  <div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                      <span className="card-label">BOUNDARY-PRESERVED CHUNKS ({chunks.length})</span>
                      <span style={{ fontSize: "12px", color: "var(--muted)" }}>Word &amp; Sentence Aware Chunking</span>
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
                  </>
                  )}
                </div>
              )}
            </div>
          </div>
        </section>
      )}
    </main>
  );
}
