import type { Provenance } from "@/lib/api/types";
import { detectLang, formatTimestamp, shortId, visibleMetadata } from "./utils";

export function Pagination({
  total,
  limit,
  offset,
  loading,
  onChange,
}: {
  total: number;
  limit: number;
  offset: number;
  loading: boolean;
  onChange: (offset: number) => void;
}) {
  if (total === 0) return null;
  const from = offset + 1;
  const to = Math.min(offset + limit, total);
  return (
    <div className="x-pager" role="navigation" aria-label="Pagination">
      <button type="button" className="secondary-button x-small-button" disabled={loading || offset <= 0} onClick={() => onChange(Math.max(0, offset - limit))}>
        ← Previous
      </button>
      <span className="x-muted">
        Showing {from}–{to} of {total}
      </span>
      <button type="button" className="secondary-button x-small-button" disabled={loading || offset + limit >= total} onClick={() => onChange(offset + limit)}>
        Next →
      </button>
    </div>
  );
}

export function Loading({ label }: { label: string }) {
  return (
    <div className="x-loading" role="status">
      <span className="x-spinner" aria-hidden="true" /> {label}
    </div>
  );
}

export function ProvenanceLine({ provenance }: { provenance: Provenance | null }) {
  if (!provenance) return <div className="x-muted x-note">No provenance recorded.</div>;
  const parts: string[] = [];
  if (provenance.model_id) parts.push(`Model ${provenance.model_id}`);
  if (provenance.prompt_version) parts.push(`Prompt ${provenance.prompt_version}`);
  if (provenance.extraction_method) parts.push(provenance.extraction_method);
  if (provenance.extracted_at) parts.push(formatTimestamp(provenance.extracted_at));
  return (
    <div className="x-muted x-note">
      Provenance: {parts.length > 0 ? parts.join(" · ") : "—"}
      {provenance.extraction_run_id && (
        <>
          {" "}
          · Run <code title={provenance.extraction_run_id}>{shortId(provenance.extraction_run_id)}</code>
        </>
      )}
    </div>
  );
}

function renderValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value) && value.every((v) => typeof v === "string" || typeof v === "number" || typeof v === "boolean")) {
    return value.map(String).join("; ");
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return "(unrenderable value)";
  }
}

/** Generic key/value view of model-controlled metadata. Internal backend keys are hidden. */
export function MetadataRows({ value, exclude = [] }: { value: Record<string, unknown> | null; exclude?: string[] }) {
  const entries = visibleMetadata(value).filter(([key]) => !exclude.includes(key));
  if (entries.length === 0) return null;
  return (
    <dl className="x-meta">
      {entries.map(([key, v]) => {
        const text = renderValue(v);
        return (
          <div key={key}>
            <dt>{key.replace(/_/g, " ")}</dt>
            <dd lang={detectLang(text)} className="x-pre">
              {text}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
