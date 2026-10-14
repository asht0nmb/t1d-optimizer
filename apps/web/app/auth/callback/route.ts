import { createServerClient } from "@supabase/ssr";
import type { EmailOtpType } from "@supabase/supabase-js";
import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { safeNextUrl } from "@/lib/auth/safe-next";

function loginRedirect(origin: string, message: string) {
  const url = new URL("/login", origin);
  url.searchParams.set("error", message);
  return NextResponse.redirect(url);
}

export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);
  const nextUrl = safeNextUrl(searchParams.get("next"), origin);
  const code = searchParams.get("code");
  const tokenHash = searchParams.get("token_hash");
  const type = searchParams.get("type");
  const authError = searchParams.get("error_description") ?? searchParams.get("error");

  if (authError) {
    return loginRedirect(origin, authError);
  }

  if (!code && !(tokenHash && type)) {
    return loginRedirect(
      origin,
      "Missing auth token. Request a new magic link and open it once.",
    );
  }

  const cookieStore = await cookies();
  // Cache-busting headers @supabase/ssr hands to setAll with the auth
  // cookies; applied to every response returned after the exchange so a
  // CDN/shared cache never stores the session Set-Cookie.
  const cacheHeaders: Record<string, string> = {};
  const withCacheHeaders = (res: NextResponse) => {
    Object.entries(cacheHeaders).forEach(([key, value]) =>
      res.headers.set(key, value),
    );
    return res;
  };
  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll();
        },
        setAll(cookiesToSet, headers) {
          cookiesToSet.forEach(({ name, value, options }) =>
            cookieStore.set(name, value, options),
          );
          Object.assign(cacheHeaders, headers ?? {});
        },
      },
    },
  );

  if (code) {
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (error) return withCacheHeaders(loginRedirect(origin, error.message));
  } else if (tokenHash && type) {
    const { error } = await supabase.auth.verifyOtp({
      token_hash: tokenHash,
      type: type as EmailOtpType,
    });
    if (error) return withCacheHeaders(loginRedirect(origin, error.message));
  }

  return withCacheHeaders(NextResponse.redirect(nextUrl));
}
