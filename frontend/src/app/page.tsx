import Link from "next/link";
import { AppShell } from "@/components/app/AppShell";
import { PageTitle } from "@/components/app/ProductPage";
import { WorkspaceDashboard } from "@/components/app/WorkspaceDashboard";
import { getSupabaseServerClient } from "@/lib/supabase/server";

export default async function HomePage() {
  const supabase = await getSupabaseServerClient();
  const { data } = supabase ? await supabase.auth.getUser() : { data: { user: null } };
  if (!data.user) return <main className="shell"><nav className="nav"><Link className="brand" href="/"><span className="brand-mark">P</span><span>pramaan<span className="brand-muted">.ai</span></span></Link><Link className="secondary-button" href="/login">Sign in</Link></nav><section className="hero"><div className="eyebrow"><span className="pulse" /> EVIDENCE-FIRST DOCUMENT INTELLIGENCE</div><h1>Operational clarity<br /><span>with evidence attached.</span></h1><p className="hero-copy">Pramaan AI turns documents into traceable facts and requirements. AI proposals remain evidence-backed and human review stays separate.</p><div className="hero-actions"><Link className="primary-button" href="/login">Sign in to your workspace <span aria-hidden="true">→</span></Link></div></section></main>;
  return <AppShell><PageTitle eyebrow="Workspace overview" title="Evidence, before action"><p>Live document and extraction metrics are scoped to your authenticated workspace.</p></PageTitle><WorkspaceDashboard /></AppShell>;
}
