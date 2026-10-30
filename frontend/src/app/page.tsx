"use client";

import { useEffect, useState } from "react";

type HealthState = "checking" | "online" | "offline";

const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export default function HomePage() {
  const [apiState, setApiState] = useState<HealthState>("checking");
  const [checkedAt, setCheckedAt] = useState<string>("");

  async function checkApi() {
    setApiState("checking");
    try {
      const response = await fetch(`${API_BASE}/health/live`, { cache: "no-store" });
      setApiState(response.ok ? "online" : "offline");
      setCheckedAt(new Date().toLocaleTimeString());
    } catch {
      setApiState("offline");
      setCheckedAt(new Date().toLocaleTimeString());
    }
  }

  useEffect(() => {
    void checkApi();
  }, []);

  const label = apiState === "online" ? "API connected" : apiState === "offline" ? "API unreachable" : "Checking API…";

  return (
    <main className="shell">
      <nav className="nav">
        <a className="brand" href="/" aria-label="Pramaan AI home">
          <span className="brand-mark">P</span>
          <span>pramaan<span className="brand-muted">.ai</span></span>
        </a>
        <div className="nav-tag">Evidence-first intelligence</div>
      </nav>

      <section className="hero">
        <div className="eyebrow"><span className="pulse" /> DOCUMENT INTELLIGENCE WORKSPACE</div>
        <h1>From fragmented documents<br /><span>to verified decisions.</span></h1>
        <p className="hero-copy">
          Investigate across documents, expose conflicting facts, and prepare actions with evidence attached.
          Every conclusion should be traceable to a source.
        </p>
        <div className="hero-actions">
          <button className="primary-button" type="button" onClick={() => window.alert("Foundation milestone only. Document ingestion is the next implementation phase.")}>Explore the workspace <span aria-hidden="true">→</span></button>
          <a className="secondary-button" href="http://127.0.0.1:8000/docs" target="_blank" rel="noreferrer">Open API docs ↗</a>
        </div>
      </section>

      <section className="grid" aria-label="Platform principles">
        <article className="feature-card"><div className="feature-icon">⌕</div><h2>Evidence, not guesses</h2><p>Answers should carry source document and page references. Unsupported claims should be flagged.</p><span className="card-label">GROUNDING</span></article>
        <article className="feature-card"><div className="feature-icon">⇄</div><h2>Find what conflicts</h2><p>Compare amounts, deadlines, obligations, and responsibilities across selected documents.</p><span className="card-label">COMPARISON</span></article>
        <article className="feature-card"><div className="feature-icon">✓</div><h2>Human-controlled actions</h2><p>AI prepares proposed actions. An authorized person reviews and approves before execution.</p><span className="card-label">CONTROL</span></article>
      </section>

      <section className="status-card" aria-live="polite">
        <div><span className={`status-dot ${apiState}`} /><strong>{label}</strong><p className="status-description">Backend health check · {API_BASE}</p></div>
        <div className="status-right"><span>{checkedAt ? `Checked ${checkedAt}` : "Local development"}</span><button className="refresh-button" type="button" onClick={() => void checkApi()}>Check again</button></div>
      </section>

      <footer className="footer"><span>Pramaan AI · Prototype foundation</span><span>Private by design · Evidence before action</span></footer>
    </main>
  );
}
