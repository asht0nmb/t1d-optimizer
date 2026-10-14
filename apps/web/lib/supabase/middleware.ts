import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

export function hasSupabaseMiddlewareEnv(): boolean {
  return Boolean(
    process.env.NEXT_PUBLIC_SUPABASE_URL &&
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
  );
}

function isLoginPath(pathname: string): boolean {
  return pathname === "/login" || pathname.startsWith("/login/");
}

/** /login, /login/..., /auth, /auth/... — segment-bounded (not /authors). */
export function isPublicAuthPath(pathname: string): boolean {
  return (
    isLoginPath(pathname) ||
    pathname === "/auth" ||
    pathname.startsWith("/auth/")
  );
}

function redirectToLogin(request: NextRequest) {
  const url = request.nextUrl.clone();
  url.pathname = "/login";
  url.searchParams.set("next", request.nextUrl.pathname);
  return NextResponse.redirect(url);
}

export async function updateSession(request: NextRequest) {
  // Fail CLOSED when env is misconfigured: without Supabase we cannot verify
  // a session, so every non-public page is sent to /login (same redirect as
  // the unauthenticated branch below). /login and /auth/* stay reachable.
  if (!hasSupabaseMiddlewareEnv()) {
    if (isPublicAuthPath(request.nextUrl.pathname)) {
      return NextResponse.next({ request });
    }
    return redirectToLogin(request);
  }

  let supabaseResponse = NextResponse.next({ request });
  // @supabase/ssr >= 0.10 passes cache-busting headers (Cache-Control /
  // Expires / Pragma) to setAll alongside auth cookies, but only on the FIRST
  // cookie write per client (later calls get {}). setAll rebuilds the
  // response each call, so accumulate and re-apply them every time so a
  // CDN/shared cache never stores a token-refresh Set-Cookie.
  const cacheHeaders: Record<string, string> = {};

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet, headers) {
          cookiesToSet.forEach(({ name, value }) =>
            request.cookies.set(name, value),
          );
          supabaseResponse = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            supabaseResponse.cookies.set(name, value, options),
          );
          Object.assign(cacheHeaders, headers ?? {});
          Object.entries(cacheHeaders).forEach(([key, value]) =>
            supabaseResponse.headers.set(key, value),
          );
        },
      },
    },
  );

  const {
    data: { user },
  } = await supabase.auth.getUser();

  const isLogin = isLoginPath(request.nextUrl.pathname);

  // The unauthenticated redirect has no session to carry.
  if (!user && !isPublicAuthPath(request.nextUrl.pathname)) {
    return redirectToLogin(request);
  }

  if (user && isLogin) {
    const url = request.nextUrl.clone();
    url.pathname = "/";
    // Carry any just-refreshed (rotated) session cookies and their cache
    // headers onto the redirect, otherwise the rotation is lost.
    const redirect = NextResponse.redirect(url);
    supabaseResponse.cookies
      .getAll()
      .forEach((cookie) => redirect.cookies.set(cookie));
    Object.entries(cacheHeaders).forEach(([key, value]) =>
      redirect.headers.set(key, value),
    );
    return redirect;
  }

  return supabaseResponse;
}
