import { createServerClient } from "@supabase/ssr";
import { createClient } from "@supabase/supabase-js";
import { cookies } from "next/headers";

/** Session-aware client for Server Components (authenticated role via JWT). */
export async function createSessionClient() {
  const cookieStore = await cookies();
  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll();
        },
        // The second `headers` arg (Cache-Control/Expires/Pragma from
        // @supabase/ssr) is deliberately not applied here: there is no
        // response object in a Server Component, and requireSession() returns
        // before the route builds its response. Page refreshes are handled
        // (with cache headers) by proxy.ts; see
        // docs/updates/2026-10-14-web-ssr-cache-headers.md for the /api gap.
        setAll(cookiesToSet) {
          try {
            cookiesToSet.forEach(({ name, value, options }) =>
              cookieStore.set(name, value, options),
            );
          } catch {
            // set from Server Component — proxy.ts refreshes session
          }
        },
      },
    },
  );
}

/** Service-role client — server-only aggregations; bypasses RLS. Never import from client components. */
export function createServiceClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) {
    throw new Error(
      "NEXT_PUBLIC_SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required",
    );
  }
  return createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}
