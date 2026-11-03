import type { EvidenceReference } from "@/lib/api/types";
import { StatusBadge, verificationTone } from "./StatusBadge";
import { detectLang, locateQuote, shortId } from "./utils";

export interface ChunkPreview {
  chunk_index: number;
  page_start: number;
  content: string;
}

const EXCERPT_CONTEXT = 200;

function Excerpt({ content, quote }: { content: string; quote: string | null | undefined }) {
  const located = locateQuote(content, quote);
  const lang = detectLang(content);
  if (located) {
    const before = located.before.length > EXCERPT_CONTEXT ? `…${located.before.slice(-EXCERPT_CONTEXT)}` : located.before;
    const after = located.after.length > EXCERPT_CONTEXT ? `${located.after.slice(0, EXCERPT_CONTEXT)}…` : located.after;
    return (
      <div className="x-excerpt" lang={lang}>
        {before}
        <mark className="x-mark">{located.match}</mark>
        {after}
      </div>
    );
  }
  const text = content.length > 400 ? `${content.slice(0, 400)}…` : content;
  return (
    <div className="x-excerpt" lang={lang}>
      {text}
      <div className="x-muted x-note">Quote not highlighted (no single unambiguous match in this preview).</div>
    </div>
  );
}

/** Evidence references for one fact/requirement. Chunk text is joined client-side when loaded. */
export function EvidenceList({
  evidence,
  chunksById,
  quote,
}: {
  evidence: EvidenceReference[];
  chunksById: Record<string, ChunkPreview>;
  quote: string | null | undefined;
}) {
  if (evidence.length === 0) {
    return <div className="x-muted">No evidence references recorded for this item.</div>;
  }
  return (
    <div className="x-evidence">
      <span className="x-label">Supporting evidence ({evidence.length})</span>
      {evidence.map((ev) => {
        const chunk = chunksById[ev.chunk_id];
        return (
          <details key={ev.evidence_link_id} className="x-evidence-item">
            <summary>
              <span>
                Page {ev.page_number} · Chunk #{ev.chunk_index}
              </span>
              <StatusBadge tone="info" title="How this chunk relates to the item">
                {ev.relationship_type}
              </StatusBadge>
              <StatusBadge tone={verificationTone(ev.verification_status)}>{ev.verification_status}</StatusBadge>
            </summary>
            <div className="x-evidence-body">
              {ev.verification_status === "UNVERIFIED" && (
                <div className="x-note">Evidence text matched — not human verified.</div>
              )}
              <div className="x-muted x-note">
                Evidence link <code title={ev.evidence_link_id}>{shortId(ev.evidence_link_id)}</code> · chunk{" "}
                <code title={ev.chunk_id}>{shortId(ev.chunk_id)}</code>
              </div>
              {chunk ? (
                <Excerpt content={chunk.content} quote={quote} />
              ) : (
                <div className="x-muted x-note">Chunk text is not loaded for this document.</div>
              )}
            </div>
          </details>
        );
      })}
    </div>
  );
}
