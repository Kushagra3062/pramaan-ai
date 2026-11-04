"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { logout } from "@/app/login/actions";

const links = [
  { href: "/", label: "Overview", index: "01" },
  { href: "/documents", label: "Documents", index: "02" },
  { href: "/extraction", label: "Extraction", index: "03" },
];

export function AppShell({ children }: Readonly<{ children: React.ReactNode }>) {
  const pathname = usePathname();
  return (
    <div className="app-frame">
      <aside className="app-sidebar">
        <Link className="brand" href="/"><span className="brand-mark">P</span><span>pramaan<span className="brand-muted">.ai</span></span></Link>
        <p className="sidebar-kicker">Evidence intelligence</p>
        <nav className="app-nav" aria-label="Application">
          {links.map((link) => <Link key={link.href} href={link.href} className={pathname === link.href ? "app-nav-link active" : "app-nav-link"}><span className="app-nav-index" aria-hidden="true">{link.index}</span>{link.label}</Link>)}
        </nav>
        <div className="sidebar-bottom"><span className="workspace-chip"><i aria-hidden="true" /> Workspace access is enforced by the API</span><form action={logout}><button className="logout-button" type="submit">Sign out <span aria-hidden="true">→</span></button></form></div>
      </aside>
      <main className="app-main">{children}</main>
    </div>
  );
}
