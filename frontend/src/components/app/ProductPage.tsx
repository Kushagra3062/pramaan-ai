import Link from "next/link";

export function PageTitle({ eyebrow, title, children }: Readonly<{ eyebrow: string; title: string; children?: React.ReactNode }>) {
  return <header className="page-title"><p className="eyebrow">{eyebrow}</p><h1>{title}</h1>{children}</header>;
}

export function CapabilityUnavailable({ title, detail, actionHref = "/extraction", action = "Open extraction" }: Readonly<{ title: string; detail: string; actionHref?: string; action?: string }>) {
  return <section className="product-empty"><div className="empty-icon" aria-hidden="true">○</div><h2>{title}</h2><p>{detail}</p><Link className="secondary-button" href={actionHref}>{action}</Link></section>;
}
