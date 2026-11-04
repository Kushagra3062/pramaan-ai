"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { logout } from "@/app/login/actions";
import { getWorkspaces, type WorkspaceMembership } from "@/lib/api/workspaces";

export function LoadRow({ text }: Readonly<{ text: string }>) {
  return <div className="dashboard-loading"><span className="x-spinner" /> {text}</div>;
}

export function ErrorCard({ title, message, onRetry }: Readonly<{ title: string; message: string; onRetry?: () => void }>) {
  return (
    <div className="product-empty">
      <div className="empty-icon" aria-hidden="true">○</div>
      <h2>{title}</h2>
      <p>{message}</p>
      {onRetry ? <button type="button" className="secondary-button" onClick={onRetry}>Retry</button> : null}
    </div>
  );
}

export function EmptyCard({ title, detail, href, action }: Readonly<{ title: string; detail: string; href?: string; action?: string }>) {
  return (
    <div className="x-empty">
      <strong>{title}</strong>
      <p className="x-note x-muted">{detail}</p>
      {href ? <p><Link className="text-link" href={href}>{action ?? "Open"}</Link></p> : null}
    </div>
  );
}

export function StatusPill({ value }: Readonly<{ value: string }>) {
  const tone = /^(READY|COMPLETED|VERIFIED|APPROVED|RESOLVED|SUPPORTED|PASSED|supported)$/.test(value)
    ? "x-badge-ok"
    : /^(FAILED|REJECTED|ERROR|error|FAILED)$/.test(value)
      ? "x-badge-err"
      : /^(OPEN|PROPOSED|PENDING|PROCESSING|UPLOADED|NEEDS_OCR|UNVERIFIED|DRAFT|mixed_evidence|PARTIAL|IN_REVIEW|CREATED|RETRIEVING|REASONING)$/.test(value)
        ? "x-badge-warn"
        : "x-badge-muted";
  return <span className={`x-badge ${tone}`}>{value.replace(/_/g, " ")}</span>;
}

export function shortId(id: string) {
  return id.length > 13 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id;
}

export function formatDate(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

const NAV = [
  { suffix: "", label: "Dashboard", index: "01" },
  { suffix: "/documents", label: "Documents", index: "02" },
  { suffix: "/investigate", label: "Investigate", index: "03" },
  { suffix: "/changes", label: "Changes", index: "04" },
  { suffix: "/actions", label: "Actions", index: "05" },
  { suffix: "/evaluation", label: "Evaluation", index: "06" },
  { suffix: "/settings", label: "Settings", index: "07" },
];

export function WorkspaceShell({ workspaceId, children }: Readonly<{ workspaceId: string; children: React.ReactNode }>) {
  const pathname = usePathname();
  const router = useRouter();
  const [memberships, setMemberships] = useState<WorkspaceMembership[]>([]);
  const [denied, setDenied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void getWorkspaces().then((result) => {
      if (cancelled) return;
      if (!result.ok) return setDenied(true);
      setMemberships(result.data);
      if (!result.data.some((item) => item.workspace_id === workspaceId)) setDenied(true);
    });
    return () => { cancelled = true; };
  }, [workspaceId]);

  const current = memberships.find((item) => item.workspace_id === workspaceId);
  const root = `/w/${workspaceId}`;
  if (denied) {
    return (
      <main className="app-main" style={{ margin: "0 auto" }}>
        <ErrorCard title="Workspace unavailable" message="You do not have access to this workspace, or it does not exist." />
        <p><Link className="text-link" href="/onboarding">Back to workspace picker</Link></p>
      </main>
    );
  }
  return (
    <div className="app-frame">
      <aside className="app-sidebar">
        <Link className="brand" href="/"><span className="brand-mark">P</span><span>pramaan<span className="brand-muted">.ai</span></span></Link>
        <label className="w-switcher">Workspace
          <select value={workspaceId} onChange={(event) => router.push(`/w/${event.target.value}`)} aria-label="Active workspace">
            {memberships.length === 0 ? <option value={workspaceId}>Loading…</option> : null}
            {memberships.map((item) => <option key={item.workspace_id} value={item.workspace_id}>{item.name}</option>)}
          </select>
        </label>
        {current ? <p className="w-role">Role: {current.role}</p> : null}
        <p className="sidebar-kicker">Workspace</p>
        <nav className="app-nav" aria-label="Workspace">
          {NAV.map((link) => {
            const href = `${root}${link.suffix}`;
            const active = link.suffix === "" ? pathname === root : pathname === href || pathname.startsWith(`${href}/`);
            return <Link key={href} href={href} className={active ? "app-nav-link active" : "app-nav-link"}><span className="app-nav-index" aria-hidden="true">{link.index}</span>{link.label}</Link>;
          })}
        </nav>
        <div className="sidebar-bottom">
          <span className="workspace-chip"><i aria-hidden="true" /> Access is enforced by the API</span>
          <form action={logout}><button className="logout-button" type="submit">Sign out <span aria-hidden="true">→</span></button></form>
        </div>
      </aside>
      <main className="app-main">{children}</main>
    </div>
  );
}
