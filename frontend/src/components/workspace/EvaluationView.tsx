"use client";

import { useEffect, useState } from "react";
import { PageTitle } from "@/components/app/ProductPage";
import { EmptyCard, LoadRow, StatusPill, formatDate } from "@/components/workspace/ui";
import { getEvalRun, listDatasets, listEvalRuns, startEvalRun, type EvalCase, type EvalDataset, type EvalRunDetail, type EvalRunReport, type EvalRunSummary } from "@/lib/api/evaluation";

function MetricBar({ label, value }: Readonly<{ label: string; value: number | null }>) {
  const percent = value === null ? 0 : Math.round(value * 100);
  return (
    <div className="w-metric">
      <div className="w-metric-head"><span>{label}</span><strong>{value === null ? "—" : value.toFixed(3)}</strong></div>
      <div className="w-bar" role="img" aria-label={`${label} ${percent} percent`}><i style={{ width: `${percent}%` }} /></div>
    </div>
  );
}

function CaseExplorer({ cases }: Readonly<{ cases: EvalCase[] }>) {
  const [open, setOpen] = useState<string | null>(null);
  const failures = cases.filter((item) => item.status !== "PASSED");
  return (
    <div className="x-card">
      <div className="x-card-head"><h3>Failure explorer</h3><span className="x-muted x-note">{failures.length} of {cases.length} need attention</span></div>
      <div className="x-table-wrap">
        <table className="x-table">
          <thead><tr><th>Case</th><th>Status</th><th>Fact F1</th><th>Req F1</th><th /></tr></thead>
          <tbody>
            {cases.map((item) => {
              const scores = item.scores as { facts?: { f1?: number | null }; requirements?: { f1?: number | null } } | null;
              return (
                <tr key={item.case_id}>
                  <td><code>{item.case_id}</code></td>
                  <td><StatusPill value={item.status} /></td>
                  <td>{scores?.facts?.f1 === null || scores?.facts?.f1 === undefined ? "—" : Number(scores.facts.f1).toFixed(2)}</td>
                  <td>{scores?.requirements?.f1 === null || scores?.requirements?.f1 === undefined ? "—" : Number(scores.requirements.f1).toFixed(2)}</td>
                  <td><button type="button" className="text-link" onClick={() => setOpen((value) => value === item.case_id ? null : item.case_id)}>{open === item.case_id ? "Hide" : "Inspect"}</button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {open ? (
        <div className="w-sides">
          <div className="w-side"><p className="x-label">Expected output</p><pre className="x-excerpt">{JSON.stringify(cases.find((item) => item.case_id === open)?.expected_output ?? null, null, 2)}</pre></div>
          <div className="w-side"><p className="x-label">Actual output</p><pre className="x-excerpt">{JSON.stringify(cases.find((item) => item.case_id === open)?.actual_output ?? null, null, 2)}</pre></div>
        </div>
      ) : null}
    </div>
  );
}

export function EvaluationView({ workspaceId }: Readonly<{ workspaceId: string }>) {
  const [datasets, setDatasets] = useState<EvalDataset[]>([]);
  const [mode, setMode] = useState<"mocked" | "live">("mocked");
  const [pushScores, setPushScores] = useState(false);
  const [running, setRunning] = useState(false);
  const [report, setReport] = useState<EvalRunReport | null>(null);
  const [runStatus, setRunStatus] = useState("");
  const [error, setError] = useState("");
  const [runs, setRuns] = useState<EvalRunSummary[]>([]);
  const [selectedRun, setSelectedRun] = useState<EvalRunDetail | null>(null);
  const [baselineId, setBaselineId] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const [loadingRuns, setLoadingRuns] = useState(true);

  useEffect(() => {
    void listDatasets(workspaceId).then((result) => { if (result.ok) setDatasets(result.data); });
    void listEvalRuns(workspaceId).then((result) => {
      setLoadingRuns(false);
      if (result.ok) setRuns(result.data.items);
    });
  }, [workspaceId]);

  const run = async () => {
    if (running) return;
    setRunning(true);
    setError("");
    setReport(null);
    const result = await startEvalRun(workspaceId, mode, pushScores);
    setRunning(false);
    if (!result.ok) return setError(result.message);
    setReport(result.data.report);
    setRunStatus(result.data.run_status);
    void listEvalRuns(workspaceId).then((list) => { if (list.ok) setRuns(list.data.items); });
  };

  const openRun = async (runId: string) => {
    setSelectedRun(null);
    const result = await getEvalRun(workspaceId, runId);
    if (result.ok) setSelectedRun(result.data);
  };

  const download = (kind: "json" | "csv") => {
    if (!report) return;
    const payload = kind === "json"
      ? JSON.stringify(report, null, 2)
      : `case_id,language,fact_f1,requirement_f1,missing_prediction\n${report.per_case.map((item) => `${item.case_id},${item.language},${item.facts.f1 ?? ""},${item.requirements.f1 ?? ""},${item.missing_prediction}`).join("\n")}\n`;
    const url = URL.createObjectURL(new Blob([payload], { type: kind === "json" ? "application/json" : "text/csv" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `eval-${report.run_id}.${kind}`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const baseline = runs.find((item) => item.id === baselineId)?.metrics as Record<string, number | null> | null | undefined;
  const candidate = runs.find((item) => item.id === candidateId)?.metrics as Record<string, number | null> | null | undefined;
  const deltaRows = baseline && candidate ? ["fact_f1", "requirement_f1", "quote_match_rate"].map((key) => {
    const before = typeof baseline[key] === "number" ? (baseline[key] as number) : null;
    const after = typeof candidate[key] === "number" ? (candidate[key] as number) : null;
    return { key, before, after, delta: before === null || after === null ? null : after - before };
  }) : [];

  return (
    <>
      <PageTitle eyebrow="Evaluation and traces" title="Evaluation">
        <p>Versioned benchmark runs with case-level failure inspection. Mocked mode is free; live mode spends model calls.</p>
      </PageTitle>
      <section className="x-card">
        <div className="x-card-head"><h3>Dataset</h3></div>
        {datasets.length === 0 ? <p className="x-note x-muted">Loading datasets…</p> : datasets.map((dataset) => (
          <dl className="x-meta" key={dataset.dataset_version}>
            <div><dt>Dataset version</dt><dd><code>{dataset.dataset_version}</code></dd></div>
            <div><dt>Evaluator</dt><dd><code>{dataset.evaluator_version}</code></dd></div>
            <div><dt>Cases</dt><dd>{dataset.case_count} ({dataset.languages.join(", ")})</dd></div>
            <div><dt>Case IDs</dt><dd><code>{dataset.case_ids.join(", ")}</code></dd></div>
          </dl>
        ))}
      </section>
      <section className="x-card">
        <div className="x-card-head"><h3>Run mode</h3></div>
        <div className="w-radio-row" role="radiogroup" aria-label="Run mode">
          <label className="w-radio"><input type="radio" checked={mode === "mocked"} onChange={() => setMode("mocked")} /> Mocked / regression <span>grades the bundled self-check baseline; no model calls, no cost.</span></label>
          <label className="w-radio"><input type="radio" checked={mode === "live"} onChange={() => setMode("live")} /> Live model <span>runs every case through Muse Spark — spends budgeted calls.</span></label>
        </div>
        {mode === "live" ? <p className="x-banner x-banner-warn">Live mode spends one budgeted model call per case ({datasets[0]?.case_count ?? 7} calls) and counts against the daily installation budget.</p> : null}
        <label className="w-check"><input type="checkbox" checked={pushScores} onChange={(event) => setPushScores(event.target.checked)} /> Push aggregate scores to Langfuse (requires configured keys; inactive otherwise)</label>
        <div className="x-actions">
          <button type="button" className="primary-button" disabled={running} onClick={() => void run()}>{running ? "Running benchmark…" : "Run benchmark"}</button>
          {report ? <><button type="button" className="secondary-button x-small-button" onClick={() => download("json")}>Export JSON</button><button type="button" className="secondary-button x-small-button" onClick={() => download("csv")}>Export CSV</button></> : null}
        </div>
        {running ? <p className="x-loading"><span className="x-spinner" /> Grading cases…</p> : null}
        {error ? <p className="x-banner x-banner-err">{error}</p> : null}
      </section>
      {report ? (
        <section className="x-card">
          <div className="x-card-head"><h3>Metrics overview</h3><StatusPill value={runStatus} /></div>
          <p className="x-note x-muted">Model {String(report.model_version)} · Prompt {String(report.prompt_version)}{report.self_check ? " · self-check baseline (expected outputs graded against themselves)" : ""} · {report.model_calls} model calls · Langfuse scores {report.scores_pushed_to_langfuse ? "pushed" : "not pushed"}</p>
          <div className="w-metrics">
            <MetricBar label="Facts F1" value={report.facts.f1} />
            <MetricBar label="Requirements F1" value={report.requirements.f1} />
            <MetricBar label="Exact quote match" value={report.evidence_grounding.exact_quote_match_rate ?? null} />
            <MetricBar label="Malayalam preservation" value={report.multilingual_integrity.malayalam_source_preservation_rate ?? null} />
          </div>
          <div className="x-table-wrap">
            <table className="x-table">
              <thead><tr><th>Case</th><th>Lang</th><th>Fact F1</th><th>Req F1</th><th>Missing</th></tr></thead>
              <tbody>{report.per_case.map((item) => <tr key={item.case_id}><td><code>{item.case_id}</code></td><td>{item.language}</td><td>{item.facts.f1 === null ? "—" : item.facts.f1.toFixed(2)}</td><td>{item.requirements.f1 === null ? "—" : item.requirements.f1.toFixed(2)}</td><td>{item.missing_prediction ? "yes" : "no"}</td></tr>)}</tbody>
            </table>
          </div>
        </section>
      ) : null}
      <section className="x-card">
        <div className="x-card-head"><h3>Run history</h3></div>
        {loadingRuns ? <LoadRow text="Loading runs…" /> : null}
        {!loadingRuns && runs.length === 0 ? <EmptyCard title="No runs yet" detail="Mocked runs are free — start one above." /> : null}
        {runs.length > 0 ? (
          <div className="x-table-wrap">
            <table className="x-table">
              <thead><tr><th>Run</th><th>Status</th><th>Mode</th><th>Fact F1</th><th>Created</th><th /></tr></thead>
              <tbody>{runs.map((item) => (
                <tr key={item.id}>
                  <td><code>{item.id.slice(0, 8)}</code></td>
                  <td><StatusPill value={item.status} /></td>
                  <td>{typeof item.metrics?.mode === "string" ? String(item.metrics.mode) : "—"}</td>
                  <td>{typeof item.metrics?.fact_f1 === "number" ? (item.metrics.fact_f1 as number).toFixed(3) : "—"}</td>
                  <td>{formatDate(item.created_at)}</td>
                  <td><button type="button" className="text-link" onClick={() => void openRun(item.id)}>Inspect</button></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : null}
      </section>
      {selectedRun ? <CaseExplorer cases={selectedRun.cases} /> : null}
      <section className="x-card">
        <div className="x-card-head"><h3>Baseline comparison</h3><span className="x-muted x-note">same dataset + evaluator only</span></div>
        <div className="w-form-row">
          <label className="w-field">Baseline run
            <select value={baselineId} onChange={(event) => setBaselineId(event.target.value)}>
              <option value="">Select…</option>{runs.map((item) => <option key={item.id} value={item.id}>{item.id.slice(0, 8)} · {item.dataset_version} · {formatDate(item.created_at)}</option>)}
            </select>
          </label>
          <label className="w-field">Candidate run
            <select value={candidateId} onChange={(event) => setCandidateId(event.target.value)}>
              <option value="">Select…</option>{runs.map((item) => <option key={item.id} value={item.id}>{item.id.slice(0, 8)} · {item.dataset_version} · {formatDate(item.created_at)}</option>)}
            </select>
          </label>
        </div>
        {deltaRows.length > 0 ? (
          <div className="x-table-wrap">
            <table className="x-table">
              <thead><tr><th>Metric</th><th>Baseline</th><th>Candidate</th><th>Δ</th></tr></thead>
              <tbody>{deltaRows.map((row) => <tr key={row.key}><td>{row.key}</td><td>{row.before === null ? "—" : row.before.toFixed(3)}</td><td>{row.after === null ? "—" : row.after.toFixed(3)}</td><td>{row.delta === null ? "—" : row.delta.toFixed(3)}</td></tr>)}</tbody>
            </table>
          </div>
        ) : <p className="x-note x-muted">Select two runs to compare. Nothing is promoted automatically.</p>}
        {baselineId && candidateId && runs.find((item) => item.id === baselineId)?.dataset_version !== runs.find((item) => item.id === candidateId)?.dataset_version ? <p className="x-banner x-banner-warn">Dataset versions differ — comparison is not valid.</p> : null}
      </section>
    </>
  );
}
