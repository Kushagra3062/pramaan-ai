"use server";

import { redirect } from "next/navigation";
import { getSupabaseServerClient } from "@/lib/supabase/server";

export async function login(formData: FormData) {
  const supabase = await getSupabaseServerClient();
  if (!supabase) return;
  const { error } = await supabase.auth.signInWithPassword({ email: String(formData.get("email") || ""), password: String(formData.get("password") || "") });
  if (error) return;
  redirect("/test");
}

export async function logout() { const supabase = await getSupabaseServerClient(); await supabase?.auth.signOut(); redirect("/login"); }
