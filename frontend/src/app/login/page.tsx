import Link from "next/link";
import { LoginForm } from "./LoginForm";

export default function LoginPage() {
  const configured = Boolean(
    process.env.NEXT_PUBLIC_SUPABASE_URL &&
      process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY,
  );

  return <main className="auth-page"><div className="auth-orbit auth-orbit-one" aria-hidden="true" /><div className="auth-orbit auth-orbit-two" aria-hidden="true" /><section className="auth-panel" aria-labelledby="sign-in-title"><Link className="brand auth-brand" href="/"><span className="brand-mark">P</span><span>pramaan<span className="brand-muted">.ai</span></span></Link><div className="auth-grid"><div className="auth-intro"><p className="eyebrow"><span className="pulse" /> Secure workspace</p><h1 id="sign-in-title">Evidence that stays connected to the source.</h1><p>Sign in to review workspace-scoped documents, their extracted facts, and the evidence that supports every proposal.</p><ul className="auth-proof-list"><li><span aria-hidden="true">↗</span> Source-linked facts and requirements</li><li><span aria-hidden="true">↗</span> Workspace authorization enforced by the API</li><li><span aria-hidden="true">↗</span> Human review remains separate from AI output</li></ul></div><div className="auth-card"><div className="auth-card-heading"><p className="card-label">Pramaan workspace</p><h2>Welcome back</h2><p>Use the Supabase account invited to your workspace.</p></div>{!configured ? <div className="setup-message" role="alert"><span className="setup-message-icon" aria-hidden="true">!</span><div><strong>Authentication setup is incomplete</strong><p>Add <code>NEXT_PUBLIC_SUPABASE_URL</code> and <code>NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY</code> to this Vercel project, then redeploy.</p></div></div> : <LoginForm />}<p className="auth-footnote">Private documents and workspace access are never shared between members.</p></div></div></section></main>;
}
