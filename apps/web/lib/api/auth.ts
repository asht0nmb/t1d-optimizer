import { createSessionClient } from "@/lib/supabase/server";

/**
 * Session guard for data API routes.
 *
 * The middleware intentionally bypasses /api/* (the cron health route uses
 * bearer-token auth instead of cookies), and the data routes query with
 * service-role / direct-pg credentials that bypass RLS — so without this
 * guard every JSON endpoint would be publicly readable. Each data route
 * MUST call this first and return the response when one is given.
 *
 * Returns null when a signed-in Supabase session is present; otherwise a
 * 401 JSON response to return as-is.
 */
export async function requireSession(): Promise<Response | null> {
  try {
    const supabase = await createSessionClient();
    const {
      data: { user },
    } = await supabase.auth.getUser();
    if (user) return null;
  } catch {
    // fall through to 401 — never fail open
  }
  return Response.json({ error: "unauthorized" }, { status: 401 });
}
