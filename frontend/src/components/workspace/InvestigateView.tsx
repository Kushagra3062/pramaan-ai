"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { PageTitle } from "@/components/app/ProductPage";
import { EmptyCard, ErrorCard, LoadRow, StatusPill, formatDate } from "@/components/workspace/ui";
import { getAnalysis, investigate, listAnalyses, type AnalysisDetail, type AnalysisSummary } from "@/lib/api/analyses";
import { listDocuments, type DocumentItem } from "@/lib/api/documents";

const EXAMPLES = [
  "What are the payment deadlines across these documents?",
  "Who is responsible for approvals?",
  "Which obligations conflict between versions?",
];

const PROGRESS = ["Retrieving evidence…", "Analyzing passages…", "Validating citations…"];

export function InvestigateView({ workspaceId }: Readonly<{ workspaceId: string }>) {
  const search = useSearchParams();
  const [question, setQuestion] = useState("");
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [scope, setScope] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<AnalysisDetail | null>(null);
  const [error, setError] = useState("");
  const [history, setHistory] = useState<AnalysisSummary[]>([]);
  const [historyError, setHistoryError] = useState("");
  const root = `/w/${workspaceId}`;

  useEffect(() => {
    const preselected = (search.get("docs") || "").split(",").map((value) => value.trim()).filter(Boolean);
    if (preselected.length > 0) setScope(preselected);
    void listDocuments(workspaceId, 0).then((list) => { if (list.ok) setDocuments(list.data.items); });
    void listAnalyses(workspaceId, 10).then((list) => {
      if (list.ok) setHistory(list.data.items);
      else setHistoryError(list.message);
    });
  }, [workspaceId, search]);

  useEffect(() => {
    if (!running) return;
    setProgress(0);
    const timer = setInterval(() => setProgress((value) => Math.min(value + 1, PROGRESS.length - 1)), 4000);
    return () => clearInterval(timer);
  }, [running]);

  const submit = async () => {
    if (running || question.trim().length < 3) return;
    setRunning(true);
    setError("");
    setResult(null);
    const response = await investigate(workspaceId, question.trim(), scope);
    setRunning(false);
    if (!response.ok) return setError(response.message);
    setResult(response.data);
    void listAnalyses(workspaceId, 10).then((list) => { if (list.ok) setHistory(list.data.items); });
  };

  const resultStatus = result?.result && typeof result.result.status === "string" ? String(result.result.status) : result?.status;
  const retrieved = result?.result && Array.isArray(result.result.retrieved_chunks) ? (result.result.retrieved_chunks as { chunk_id: string; document_id: string; filename: string; page_start: number; page_end: number; rank: number }[]) : [];

  return (
    <>
      <PageTitle eyebrow="Investigate" title="Ask across evidence">
        <p>Grounded answers with per-claim citations. Insufficient evidence abstains — it never guesses.</p>
      </PageTitle>
      <section className="x-card">
        <div className="x-card-head"><h3>Corpus scope</h3><span className="x-muted x-note">{scope.length === 0 ? "Whole workspace" : `${scope.length} document(s)`}</span></div>
        <div className="w-scope">
          {documents.length === 0 ? <p className="x-note x-muted">No documents to scope yet.</p> : documents.slice(0, 20).map((doc) => (
            <label key={doc.id} className="w-check"><input type="checkbox" checked={scope.includes(doc.id)} onChange={() => setScope((prev) => prev.includes(doc.id) ? prev.filter((id) => id !== doc.id) : [...prev, doc.id])} /> {doc.original_filename}</label>
          ))}
        </div>
        {scope.length > 0 ? <button type="button" className="text-link" onClick={() => setScope([])}>Clear scope (whole workspace)</button> : null}
      </section>
      <section className="x-card">
        <div className="x-card-head"><h3>Question</h3><span className="x-muted x-note">{question.trim().length}/2000</span></div>
        <textarea className="w-textarea" rows={3} maxLength={2000} value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Ask about obligations, deadlines, owners…" aria-label="Investigation question" />
        <div className="w-examples">
          {EXAMPLES.map((example) => <button key={example} type="button" className="secondary-button x-small-button" onClick={() => setQuestion(example)}>{example}</button>)}
        </div>
        <div className="x-actions">
          <button type="button" className="primary-button" disabled={running || question.trim().length < 3} onClick={() => void submit()}>{running ? "Running…" : "Submit"}</button>
          <button type="button" className="secondary-button" disabled={running} onClick={() => { setQuestion(""); setResult(null); setError(""); }}>Clear</button>
          {running ? <span className="x-loading"><span className="x-spinner" /> {PROGRESS[progress]}</span> : null}
        </div>
        {error ? <p className="x-banner x-banner-err">{error}</p> : null}
      </section>
      {result ? (
        <section className="x-card">
          <div className="x-card-head"><h3>Result</h3><StatusPill value={resultStatus ?? "UNKNOWN"} /></div>
          <p className="w-answer">{typeof result.result?.answer === "string" ? String(result.result.answer) : "No answer text."}</p>
          <dl className="x-meta">
            <div><dt>Claims</dt><dd>{result.claims.length} ({result.claims.filter((claim) => claim.support_status !== "SUPPORTED").length} unsupported)</dd></div>
            <div><dt>Retrieved passages</dt><dd>{retrieved.length} · {typeof result.result?.retrieval_method === "string" ? String(result.result.retrieval_method) : "—"}</dd></div>
            <div><dt>Model</dt><dd>{result.model_id ?? "—"} · {result.prompt_version ?? "—"}</dd></div>
          </dl>
          <div className="x-actions"><Link className="primary-button" href={`${root}/packets/${result.id}`}>Open Decision Packet →</Link></div>
        </section>
      ) : null}
      <section className="x-card">
        <div className="x-card-head"><h3>Recent analyses</h3></div>
        {historyError ? <ErrorCard title="Could not load history" message={historyError} /> : null}
        {!historyError && history.length === 0 ? <EmptyCard title="No analyses yet" detail="Your investigation history appears here." /> : (
          <div className="x-list">{history.map((item) => (
            <Link key={item.id} className="x-item w-row-link" href={`${root}/packets/${item.id}`}>
              <div className="x-item-head"><StatusPill value={item.status} /><span className="x-muted x-note">{formatDate(item.created_at)}</span></div>
              <p className="x-note">{item.question}</p>
            </Link>
          ))}</div>
        )}
      </section>
    </>
  );
}

export function PacketView({ workspaceId, analysisId }: Readonly<{ workspaceId: string; analysisId: string }>) {
  const [packet, setPacket] = useState<AnalysisDetail | null>(null);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const root = `/w/${workspaceId}`;

  useEffect(() => {
    let cancelled = false;
    void getAnalysis(workspaceId, analysisId).then((result) => {
      if (cancelled) return;
      if (!result.ok) return setError(result.message);
      setPacket(result.data);
    });
    return () => { cancelled = true; };
  }, [workspaceId, analysisId]);

  if (error) {
    return (
      <>
        <PageTitle eyebrow="Decision Packet" title="Packet" />
        <ErrorCard title="Could not load packet" message={error} />
      </>
    );
  }
  if (!packet) {
    return (
      <>
        <PageTitle eyebrow="Decision Packet" title="Packet" />
        <LoadRow text="Loading decision packet…" />
      </>
    );
  }
  const status = packet.result && typeof packet.result.status === "string" ? String(packet.result.status) : packet.status;
  const answer = packet.result && typeof packet.result.answer === "string" ? String(packet.result.answer) : "No answer recorded.";
  const unknowns = packet.result && Array.isArray(packet.result.unknowns) ? (packet.result.unknowns as string[]) : [];
  const drafts = packet.result && Array.isArray(packet.result.proposed_actions) ? (packet.result.proposed_actions as { description: string; owner?: string | null; due_date?: string | null }[]) : [];
  const retrievalMethod = packet.result && typeof packet.result.retrieval_method === "string" ? String(packet.result.retrieval_method) : "—";

  const download = (kind: "json" | "md") => {
    const payload = kind === "json"
      ? JSON.stringify(packet, null, 2)
      : `# Decision Packet\n\nQuestion: ${packet.question}\nStatus: ${status}\nModel: ${packet.model_id ?? "—"} (${packet.prompt_version ?? "—"})\nGenerated: ${packet.created_at}\n\n## Answer\n${answer}\n\n## Claims\n${packet.claims.map((claim) => `- [${claim.support_status}] ${claim.claim_text}`).join("\n")}\n\n## Unknowns\n${unknowns.map((item) => `- ${item}`).join("\n")}\n`;
    const url = URL.createObjectURL(new Blob([payload], { type: kind === "json" ? "application/json" : "text/markdown" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `packet-${packet.id}.${kind}`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <>
      <PageTitle eyebrow="Decision Packet" title="Executive answer">
        <p>{packet.question}</p>
      </PageTitle>
      <section className="x-card">
        <div className="x-card-head"><h3>Answer</h3><StatusPill value={status} /></div>
        <p className="w-answer">{answer}</p>
      </section>
      <section className="x-card">
        <div className="x-card-head"><h3>Claim ledger</h3><span className="x-muted x-note">{packet.claims.length} claims</span></div>
        {packet.claims.length === 0 ? <EmptyCard title="No claims" detail="Abstained packets carry no claims — see unknowns below." /> : (
          <div className="x-list">{packet.claims.map((claim) => (
            <div key={claim.id} className="x-item">
              <div className="x-item-head"><StatusPill value={claim.support_status} /></div>
              <p className="x-note">{claim.claim_text}</p>
              <div className="x-evidence">
                {claim.evidence.length === 0 ? <p className="x-note x-muted">Unsupported — no retrieved passage backs this claim.</p> : claim.evidence.map((item) => (
                  <details key={`${claim.id}-${item.chunk_id}`} className="x-evidence-item">
                    <summary><span className="x-badge x-badge-info">{item.document_filename} · p.{item.page_start}–{item.page_end}</span><span className="x-muted">{item.evidence_role}</span></summary>
                    <div className="x-evidence-body">
                      <p className="x-excerpt">{item.excerpt}</p>
                      <p><Link className="text-link" href={`${root}/documents/${item.document_id}`}>Open source document →</Link></p>
                    </div>
                  </details>
                ))}
              </div>
            </div>
          ))}</div>
        )}
      </section>
      <section className="w-columns">
        <div className="x-card">
          <div className="x-card-head"><h3>Unknowns</h3></div>
          {unknowns.length === 0 ? <p className="x-note x-muted">None recorded.</p> : <ul className="x-ul">{unknowns.map((item) => <li key={item.slice(0, 60)}>{item}</li>)}</ul>}
        </div>
        <div className="x-card">
          <div className="x-card-head"><h3>Action proposals</h3><span className="x-muted x-note">Drafts only</span></div>
          {drafts.length === 0 ? <p className="x-note x-muted">No drafts proposed.</p> : (
            <div className="x-list">{drafts.map((draft, index) => (
              <div key={`${draft.description.slice(0, 40)}-${index}`} className="x-item">
                <p className="x-note">{draft.description}</p>
                <p className="x-note x-muted">Owner: {draft.owner ?? "—"} · Due: {draft.due_date ?? "—"}</p>
                <p><Link className="text-link" href={`${root}/actions?draft=${encodeURIComponent(draft.description)}`}>Propose as action →</Link></p>
              </div>
            ))}</div>
          )}
        </div>
      </section>
      <section className="x-card">
        <div className="x-card-head"><h3>Technical trace</h3></div>
        <dl className="x-meta">
          <div><dt>Model</dt><dd>{packet.model_id ?? "—"}</dd></div>
          <div><dt>Prompt version</dt><dd>{packet.prompt_version ?? "—"}</dd></div>
          <div><dt>Retrieval</dt><dd>{retrievalMethod}</dd></div>
          <div><dt>Completed</dt><dd>{formatDate(packet.completed_at)}</dd></div>
        </dl>
        <p className="x-note x-muted">Inputs and document text are redacted from traces. Langfuse receives identifiers, counts and outcomes only.</p>
      </section>
      <section className="x-card">
        <div className="x-card-head"><h3>Feedback and sharing</h3></div>
        <div className="x-actions">
          {(["useful", "not useful", "citation wrong", "missing document"] as const).map((option) => (
            <button key={option} type="button" className="secondary-button x-small-button" onClick={() => setFeedback(option)}>{option}</button>
          ))}
        </div>
        {feedback ? <p className="x-banner x-banner-info">Recorded “{feedback}” locally for this demo session. Dataset promotion is out of scope.</p> : null}
        <div className="x-actions">
          <button type="button" className="secondary-button x-small-button" onClick={() => void navigator.clipboard.writeText(`${window.location.origin}${root}/packets/${packet.id}`).then(() => setCopied(true))}>{copied ? "Link copied" : "Copy internal link"}</button>
          <button type="button" className="secondary-button x-small-button" onClick={() => download("json")}>Export JSON</button>
          <button type="button" className="secondary-button x-small-button" onClick={() => download("md")}>Export Markdown</button>
        </div>
      </section>
    </>
  );
}
