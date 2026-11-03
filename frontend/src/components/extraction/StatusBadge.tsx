import type { ReactNode } from "react";

export type BadgeTone = "ok" | "warn" | "err" | "info" | "muted";

export function StatusBadge({ tone = "muted", title, children }: { tone?: BadgeTone; title?: string; children: ReactNode }) {
  return (
    <span className={`x-badge x-badge-${tone}`} title={title}>
      {children}
    </span>
  );
}

/** Tone for backend verification statuses. UNVERIFIED is never styled as approved. */
export function verificationTone(status: string): BadgeTone {
  if (status === "VERIFIED") return "ok";
  if (status === "REJECTED") return "err";
  return "warn";
}

export function lifecycleTone(status: string): BadgeTone {
  if (status === "ACTIVE") return "ok";
  if (status === "DRAFT") return "warn";
  if (status === "SUPERSEDED" || status === "INACTIVE") return "muted";
  return "info";
}

export function runTone(status: string): BadgeTone {
  if (status === "COMPLETED") return "ok";
  if (status === "PARTIAL") return "warn";
  if (status === "FAILED") return "err";
  return "muted";
}

export function pageTone(status: string): BadgeTone {
  if (status === "PROCESSED") return "ok";
  if (status === "FAILED") return "err";
  if (status === "SKIPPED" || status === "NOT_PROCESSED") return "warn";
  return "muted";
}
