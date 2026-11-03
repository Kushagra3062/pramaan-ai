"use client";

import { useActionState } from "react";
import { useFormStatus } from "react-dom";
import { login, type LoginState } from "./actions";

const initialState: LoginState = { error: null };

function SubmitButton() {
  const { pending } = useFormStatus();
  return <button className="primary-button auth-submit" type="submit" disabled={pending}>{pending ? "Signing in…" : <>Continue securely <span aria-hidden="true">→</span></>}</button>;
}

export function LoginForm() {
  const [state, formAction] = useActionState(login, initialState);
  return <form action={formAction} className="auth-form"><label>Work email<input name="email" type="email" autoComplete="email" placeholder="you@company.com" required /></label><label>Password<input name="password" type="password" autoComplete="current-password" placeholder="Enter your password" required /></label>{state.error ? <p className="auth-error" role="alert">{state.error}</p> : null}<SubmitButton /></form>;
}
