"use server";

import { redirect } from "next/navigation";
import { getSupabaseServerClient } from "@/lib/supabase/server";

export type LoginState = { error: string | null };

export async function login(_: LoginState, formData: FormData): Promise<LoginState> {
  const supabase = await getSupabaseServerClient();
  if (!supabase) return { error: "Authentication is not configured for this deployment." };
  const email = String(formData.get("email") || "").trim();
  const password = String(formData.get("password") || "");
  if (!email || !password) return { error: "Enter both your email address and password." };
  const { error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) return { error: "We could not sign you in. Check your email and password, then try again." };
  redirect("/");
}

export async function logout() { const supabase = await getSupabaseServerClient(); await supabase?.auth.signOut(); redirect("/login"); }
