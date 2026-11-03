import type { ExtractionRun } from "@/lib/api/types";
import { StatusBadge, pageTone, runTone } from "./StatusBadge";
import { formatDuration, formatTimestamp, num, reasonText, shortId, skipReasonText } from "./utils";

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="x-stat">
      <div className="x-stat-value">{value}</div>
      <div className="x-stat-label">{label}</div>
    </div>
  );
}

function headline(run: ExtractionRun): { tone: "ok" | "warn" | "err"; text: string } {
  if (run.status === "COMPLETED") {
    return { tone: "ok", text: "Extraction completed — every page was processed." };
  }
  if (run.status === "PARTIAL") {
    return {
      tone: "warn",
      text: `Partial extraction — ${num(run.pages_processed)} of ${num(run.page_count)} page(s) processed. Results below are incomplete.`,
    };
  }
  if (run.status === "FAILED") {
    return { tone: "err", text: "Extraction failed — no page was processed successfully." };
  }
  return { tone: "warn", text: `Extraction finished with status ${String(run.status)}.` };
}

export function RunSummary({ run, httpStatus }: { run: ExtractionRun; httpStatus: number | null }) {
  const head = headline(run);
  return (
    <section className="x-card" aria-label="Extraction run summary">
      <div className="x-card-head">
        <h3>Extraction run summary</h3>
        <StatusBadge tone={runTone(run.status)}>{String(run.status)}</StatusBadge>
      </div>
      <div className={`x-banner x-banner-${head.tone}`} role="status">
        {head.text}
        {httpStatus !== null && httpStatus !== 200 && <span className="x-muted"> (HTTP {httpStatus})</span>}
      </div>

      <dl className="x-meta">
        <div>
          <dt>Run ID</dt>
          <dd>
            <code title={run.extraction_run_id}>{run.extraction_run_id}</code>
          </dd>
        </div>
        <div>
          <dt>Model</dt>
          <dd>{run.model_id}</dd>
        </div>
        <div>
          <dt>Prompt version</dt>
          <dd>{run.prompt_version}</dd>
        </div>
        <div>
          <dt>Duration</dt>
          <dd>{formatDuration(run.duration_ms)}</dd>
        </div>
        <div>
          <dt>Started</dt>
          <dd>{formatTimestamp(run.started_at)}</dd>
        </div>
        <div>
          <dt>Completed</dt>
          <dd>{formatTimestamp(run.completed_at)}</dd>
        </div>
      </dl>

      <div className="x-grid">
        <Stat label="Pages processed" value={num(run.pages_processed)} />
        <Stat label="Pages skipped" value={num(run.pages_skipped)} />
        <Stat label="Pages failed" value={num(run.pages_failed)} />
        <Stat label="Pages not processed" value={num(run.pages_not_processed)} />
        <Stat label="Model calls succeeded" value={num(run.model_requests_succeeded)} />
        <Stat label="Model calls failed" value={num(run.model_requests_failed)} />
        <Stat label="Facts created" value={num(run.facts_created)} />
        <Stat label="Facts reused" value={num(run.facts_reused)} />
        <Stat label="Requirements created" value={num(run.requirements_created)} />
        <Stat label="Requirements reused" value={num(run.requirements_reused)} />
        <Stat label="Evidence links created" value={num(run.evidence_links_created)} />
        <Stat label="Proposals skipped" value={num(run.proposals_skipped)} />
      </div>

      <div className="x-table-wrap">
        <table className="x-table">
          <thead>
            <tr>
              <th>Page</th>
              <th>Status</th>
              <th>Page extraction</th>
              <th>Chunks</th>
              <th>Model calls ok/failed</th>
              <th>Facts new/reused</th>
              <th>Reqs new/reused</th>
              <th>Proposals skipped</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {run.pages.map((p) => {
              const skipEntries = Object.entries(p.skip_reasons ?? {});
              return (
                <tr key={p.page_number}>
                  <td>{p.page_number}</td>
                  <td>
                    <StatusBadge tone={pageTone(p.status)}>{String(p.status)}</StatusBadge>
                  </td>
                  <td>{p.page_extraction_status ?? "—"}</td>
                  <td>{num(p.chunks_processed)}</td>
                  <td>
                    {num(p.requests_succeeded)} / {num(p.requests_failed)}
                  </td>
                  <td>
                    {num(p.facts_created)} / {num(p.facts_reused)}
                  </td>
                  <td>
                    {num(p.requirements_created)} / {num(p.requirements_reused)}
                  </td>
                  <td>
                    {num(p.proposals_skipped)}
                    {skipEntries.length > 0 && (
                      <div className="x-muted x-note">
                        {skipEntries.map(([code, n]) => `${skipReasonText(code)} ×${num(n)}`).join("; ")}
                      </div>
                    )}
                  </td>
                  <td>{p.reason ? reasonText(p.reason) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="x-muted x-note">
        This summary exists only in this browser session and cannot be reloaded later. Run <code>{shortId(run.extraction_run_id)}</code>
        {" "}is recorded in server-side audit data.
      </div>
    </section>
  );
}
