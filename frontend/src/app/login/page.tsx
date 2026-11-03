import { login } from "./actions";

export default function LoginPage() {
  const configured = Boolean(process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY);
  return <main className="shell"><section className="hero"><h1>Sign in to Pramaan AI</h1>{!configured ? <p>Supabase authentication is not configured. Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY.</p> : <form action={login} className="feature-card"><label>Email<input name="email" type="email" required /></label><label>Password<input name="password" type="password" required /></label><button className="primary-button" type="submit">Sign in</button></form>}</section></main>;
}
