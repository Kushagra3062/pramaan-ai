"use client";

import { useEffect, useState } from "react";
import { listFacts } from "@/lib/api/extraction";
import { FACT_TYPES, VERIFICATION_STATUSES } from "@/lib/api/types";
import type { ExtractedFact, FactType, Paginated, VerificationStatus } from "@/lib/api/types";
import { EvidenceList } from "./EvidenceList";
import type { ChunkPreview } from "./EvidenceList";
import { Loading, MetadataRows, Pagination, ProvenanceLine } from "./ListControls";
import { QuoteBlock } from "./QuoteBlock";
import { StatusBadge, verificationTone } from "./StatusBadge";
import { detectLang, visibleMetadata } from "./utils";

const PAGE_SIZE = 10;

function FactCard({ fact, chunksById }: { fact: ExtractedFact; chunksById: Record<string, ChunkPreview> }) {
  const hasMetadata = visibleMetadata(fact.normalized_value).length > 0;
  return (
    <article className="x-item">
      <div className="x-item-head">
        <StatusBadge tone="info">{fact.fact_type}</StatusBadge>
        <StatusBadge tone="muted">{fact.page_number !== null ? `Page ${fact.page_number}` : "Page not recorded"}</StatusBadge>
        <StatusBadge tone={verificationTone(fact.verification_status)}>{fact.verification_status}</StatusBadge>
      </div>
      <div className="x-value" lang={detectLang(fact.raw_value)}>
        {fact.raw_value}
      </div>
      {fact.verification_status === "UNVERIFIED" && <div className="x-note">Model-proposed — not human reviewed.</div>}
      {hasMetadata ? (
        <MetadataRows value={fact.normalized_value} />
      ) : (
        <div className="x-muted x-note">No additional normalized metadata.</div>
      )}
      <QuoteBlock text={fact.source_quote} />
      <EvidenceList evidence={fact.evidence} chunksById={chunksById} quote={fact.source_quote} />
      <ProvenanceLine provenance={fact.provenance} />
    </article>
  );
}

export function FactsList({
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
  const [factType, setFactType] = useState<FactType | "">("");
  const [verification, setVerification] = useState<VerificationStatus | "">("");
  const [offset, setOffset] = useState(0);
  const [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<Paginated<ExtractedFact> | null>(null);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    void listFacts(workspaceId, documentId, {
      limit: PAGE_SIZE,
      offset,
      fact_type: factType,
      verification_status: verification,
    }).then((res) => {
      if (cancelled) return; // stale response (document/filter/page changed)
      if (res.ok) setData(res.data);
      else setError(res.message);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [active, workspaceId, documentId, offset, factType, verification, refreshToken, reload]);

  const filtered = factType !== "" || verification !== "";
  return (
    <section className="x-card" aria-label="Extracted facts">
      <div className="x-card-head">
        <h3>Extracted facts{data ? ` (${data.total})` : ""}</h3>
        <div className="x-filters">
          <label>
            <span className="x-label">Fact type</span>
            <select
              value={factType}
              onChange={(e) => {
                setFactType(e.target.value as FactType | "");
                setOffset(0);
              }}
            >
              <option value="">All types</option>
              {FACT_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="x-label">Verification</span>
            <select
              value={verification}
              onChange={(e) => {
                setVerification(e.target.value as VerificationStatus | "");
                setOffset(0);
              }}
            >
              <option value="">All statuses</option>
              {VERIFICATION_STATUSES.map((s) => (
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

      {loading && <Loading label="Loading facts…" />}
      {error && (
        <div className="x-banner x-banner-err" role="alert">
          Could not load facts. {error}
        </div>
      )}
      {!error && data && data.items.length === 0 && !loading && (
        <div className="x-empty">
          {filtered ? "No facts match these filters." : "No facts extracted for this document yet."}
        </div>
      )}
      {!error && data && data.items.length > 0 && (
        <div className="x-list">
          {data.items.map((fact) => (
            <FactCard key={fact.fact_id} fact={fact} chunksById={chunksById} />
          ))}
        </div>
      )}
      {data && !error && <Pagination total={data.total} limit={data.limit} offset={data.offset} loading={loading} onChange={setOffset} />}
    </section>
  );
}
