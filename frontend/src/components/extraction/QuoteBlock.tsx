import { detectLang } from "./utils";

/** Verbatim source quote. Rendered as text (never HTML), with Malayalam-safe wrapping. */
export function QuoteBlock({
  text,
  label = "Source quote",
  langHint,
}: {
  text: string | null | undefined;
  label?: string;
  langHint?: string | null;
}) {
  return (
    <div className="x-quote-wrap">
      <span className="x-label">{label}</span>
      {text ? (
        <blockquote className="x-quote" lang={detectLang(text, langHint)}>
          {text}
        </blockquote>
      ) : (
        <div className="x-muted">Not stated in source</div>
      )}
    </div>
  );
}
