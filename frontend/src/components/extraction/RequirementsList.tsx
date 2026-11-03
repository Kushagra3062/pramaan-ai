"use client";

import { useEffect, useState } from "react";
import { listRequirements } from "@/lib/api/extraction";
import { REQUIREMENT_STATUSES } from "@/lib/api/types";
import type { Paginated, Requirement, RequirementStatus } from "@/lib/api/types";
import { EvidenceList } from "./EvidenceList";
import type { ChunkPreview } from "./EvidenceList";
import { Loading, MetadataRows, Pagination, ProvenanceLine } from "./ListControls";
import { QuoteBlock } from "./QuoteBlock";
import { StatusBadge, lifecycleTone, verificationTone } from "./StatusBadge";
import { asText, detectLang } from "./utils";

const PAGE_SIZE = 10;
const KNOWN_KEYS = [
  "responsible_actor",
  "required_action",
  "target_entity",
  "modality",
  "conditions",
  "deadline",
  "frequency",
  "scope",
  "original_language",
  "original_language_text",
];
const NOT_STATED = "Not stated in source";

function Field({ label, text, lang }: { label: string; text: string | null; lang?: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd lang={text ? lang : undefined} className={text ? "x-pre" : "x-muted"}>
        {text ?? NOT_STATED}
      </dd>
    </div>
  );
}

function pagesOf(req: Requirement): string {
  const fromEvidence = Array.from(new Set(req.evidence.map((e) => e.page_number))).sort((a, b) => a - b);
  if (fromEvidence.length > 0) return `Page ${fromEvidence.join(", ")}`;
  if (req.provenance?.page_number != null) return `Page ${req.provenance.page_number}`;
  return "Page not recorded";
}

function RequirementCard({ req, chunksById }: { req: Requirement; chunksById: Record<string, ChunkPreview> }) {
  const meta = req.normalized_value ?? {};
  const conditions = Array.isArray(meta.conditions)
    ? meta.conditions.filter((c): c is string => typeof c === "string" && c.trim() !== "")
    : [];
  const language = asText(meta.original_language);
  const originalText = asText(meta.original_language_text);
  const review = req.review_status;
  return (
    <article className="x-item">
      <div className="x-item-head">
        <StatusBadge tone="info">{req.requirement_type}</StatusBadge>
        <StatusBadge tone="muted">{pagesOf(req)}</StatusBadge>
        <StatusBadge tone={lifecycleTone(req.status)} title="Requirement lifecycle status">
          {req.status}
        </StatusBadge>
        <StatusBadge tone={review ? verificationTone(review === "PENDING_REVIEW" ? "UNVERIFIED" : review) : "muted"} title="Review status">
          {review ?? "Review status not recorded"}
        </StatusBadge>
      </div>
      <h4 className="x-title" lang={detectLang(req.title)}>
        {req.title}
      </h4>
      <div className="x-pre" lang={detectLang(req.description)}>
        {req.description}
      </div>
      {req.status === "DRAFT" && <div className="x-note">DRAFT — Pending human review. Model-proposed; not approved.</div>}

      <dl className="x-meta">
        <Field label="Responsible actor" text={asText(meta.responsible_actor)} lang={detectLang(asText(meta.responsible_actor), language)} />
        <Field label="Required action" text={asText(meta.required_action)} lang={detectLang(asText(meta.required_action), language)} />
        <Field label="Target entity" text={asText(meta.target_entity)} lang={detectLang(asText(meta.target_entity), language)} />
        <Field label="Modality" text={asText(meta.modality)} />
        <Field label="Deadline" text={asText(meta.deadline)} lang={detectLang(asText(meta.deadline), language)} />
        <Field label="Frequency" text={asText(meta.frequency)} lang={detectLang(asText(meta.frequency), language)} />
        <Field label="Scope" text={asText(meta.scope)} lang={detectLang(asText(meta.scope), language)} />
        <div>
          <dt>Conditions</dt>
          <dd className={conditions.length ? undefined : "x-muted"}>
            {conditions.length ? (
              <ul className="x-ul">
                {conditions.map((c, i) => (
                  <li key={i} lang={detectLang(c, language)}>
                    {c}
                  </li>
                ))}
              </ul>
            ) : (
              NOT_STATED
            )}
          </dd>
        </div>
        <Field label="Original language" text={language} />
        <Field label="Original-language text" text={originalText} lang={detectLang(originalText, language)} />
      </dl>
      <MetadataRows value={req.normalized_value} exclude={KNOWN_KEYS} />

      <QuoteBlock text={req.source_quote} langHint={language} />
      <EvidenceList evidence={req.evidence} chunksById={chunksById} quote={req.source_quote} />
      <ProvenanceLine provenance={req.provenance} />
    </article>
  );
}

export function RequirementsList({
  workspaceId,
  documentId,
  chunksById,
  refreshToken,
  active,
}: {
  workspaceId: string;
  documentId: string;
  chunksById: Record<string, ChunkPreview>;
  refreshToken: number;
  active: boolean;
}) {
  const [status, setStatus] = useState<RequirementStatus | "">("");
  const [offset, setOffset] = useState(0);
  const [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<Paginated<Requirement> | null>(null);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    void listRequirements(workspaceId, documentId, { limit: PAGE_SIZE, offset, status }).then((res) => {
      if (cancelled) return; // stale response (document/filter/page changed)
      if (res.ok) setData(res.data);
      else setError(res.message);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [active, workspaceId, documentId, offset, status, refreshToken, reload]);

  return (
    <section className="x-card" aria-label="Extracted requirements">
      <div className="x-card-head">
        <h3>Extracted requirements{data ? ` (${data.total})` : ""}</h3>
        <div className="x-filters">
          <label>
            <span className="x-label">Lifecycle status</span>
            <select
              value={status}
              onChange={(e) => {
                setStatus(e.target.value as RequirementStatus | "");
                setOffset(0);
              }}
            >
              <option value="">All statuses</option>
              {REQUIREMENT_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="refresh-button" disabled={loading} onClick={() => setReload((n) => n + 1)}>
            Refresh
          </button>
        </div>
      </div>

      {loading && <Loading label="Loading requirements…" />}
      {error && (
        <div className="x-banner x-banner-err" role="alert">
          Could not load requirements. {error}
        </div>
      )}
      {!error && data && data.items.length === 0 && !loading && (
        <div className="x-empty">
          {status !== "" ? "No requirements match this status." : "No requirements extracted for this document yet."}
        </div>
      )}
      {!error && data && data.items.length > 0 && (
        <div className="x-list">
          {data.items.map((req) => (
            <RequirementCard key={req.requirement_id} req={req} chunksById={chunksById} />
          ))}
        </div>
      )}
      {data && !error && <Pagination total={data.total} limit={data.limit} offset={data.offset} loading={loading} onChange={setOffset} />}
    </section>
  );
}
