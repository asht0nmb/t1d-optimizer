import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * @supabase/ssr 0.12 passes cache-busting headers as the 2nd setAll arg on
 * the first cookie write. These tests prove proxy.ts and /auth/callback put
 * them on the response, leave responses untouched when none are given, and
 * fail closed when Supabase env is missing. Also covers the callback's
 * `next` open-redirect guard.
 */

type SetAll = (
  cookies: { name: string; value: string; options: object }[],
  headers: Record<string, string>,
) => void;

const CACHE = {
  "Cache-Control": "private, no-cache, no-store, must-revalidate, max-age=0",
  Expires: "0",
  Pragma: "no-cache",
};
const COOKIE = [{ name: "sb-x-auth-token", value: "v", options: { path: "/" } }];

// Each test sets `writes`: the sequence of setAll(cookies, headers) calls the
// fake client performs during getUser / exchangeCodeForSession / verifyOtp.
let writes: Record<string, string>[] = [];
let user: { id: string } | null = null;
let authError: { message: string } | null = null;

vi.mock("@supabase/ssr", () => ({
  createServerClient: (
    _u: string,
    _k: string,
    opts: { cookies: { setAll: SetAll } },
  ) => {
    const flush = () => writes.forEach((h) => opts.cookies.setAll(COOKIE, h));
    return {
      auth: {
        getUser: async () => {
          flush();
          return { data: { user } };
        },
        exchangeCodeForSession: async () => {
          flush();
          return { error: authError };
        },
        verifyOtp: async () => {
          flush();
          return { error: authError };
        },
      },
    };
  },
}));

const cookieSet = vi.fn();
vi.mock("next/headers", () => ({
  cookies: async () => ({ getAll: () => [], set: cookieSet }),
}));

import { NextRequest } from "next/server";
import { proxy } from "@/proxy";
import { GET as callback } from "@/app/auth/callback/route";
import { safeNextUrl } from "@/lib/auth/safe-next";

function expectCacheHeaders(res: Response) {
  expect(res.headers.get("cache-control")).toBe(CACHE["Cache-Control"]);
  expect(res.headers.get("expires")).toBe("0");
  expect(res.headers.get("pragma")).toBe("no-cache");
}
function expectNoCacheHeaders(res: Response) {
  expect(res.headers.get("cache-control")).toBeNull();
  expect(res.headers.get("expires")).toBeNull();
  expect(res.headers.get("pragma")).toBeNull();
}

beforeEach(() => {
  writes = [];
  user = null;
  authError = null;
  cookieSet.mockClear();
  process.env.NEXT_PUBLIC_SUPABASE_URL = "http://localhost";
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = "placeholder-anon-key";
});

describe("proxy: setAll cache headers", () => {
  it("applies the headers setAll provides alongside refreshed cookies", async () => {
    user = { id: "u1" };
    writes = [CACHE];
    const res = await proxy(new NextRequest("http://localhost/trends"));
    expect(res.status).toBe(200);
    expectCacheHeaders(res);
    expect(res.headers.get("set-cookie")).toContain("sb-x-auth-token=v");
  });

  it("keeps the headers when a later setAll call passes {} (response rebuilt)", async () => {
    user = { id: "u1" };
    writes = [CACHE, {}];
    const res = await proxy(new NextRequest("http://localhost/trends"));
    expectCacheHeaders(res);
  });

  it("adds nothing when setAll passes no headers", async () => {
    user = { id: "u1" };
    writes = [{}];
    const res = await proxy(new NextRequest("http://localhost/trends"));
    expect(res.status).toBe(200);
    expectNoCacheHeaders(res);
  });

  it("adds nothing when setAll is never called", async () => {
    user = { id: "u1" };
    const res = await proxy(new NextRequest("http://localhost/trends"));
    expect(res.status).toBe(200);
    expectNoCacheHeaders(res);
  });

  it("unauthenticated redirect to /login is unchanged", async () => {
    const res = await proxy(new NextRequest("http://localhost/trends"));
    expect(res.status).toBe(307);
    const loc = new URL(res.headers.get("location")!);
    expect(loc.pathname).toBe("/login");
    expect(loc.searchParams.get("next")).toBe("/trends");
  });
});

describe("proxy: fails closed without Supabase env", () => {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  beforeEach(() => {
    delete process.env.NEXT_PUBLIC_SUPABASE_URL;
    delete process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  });
  afterEach(() => {
    process.env.NEXT_PUBLIC_SUPABASE_URL = url;
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = anon;
  });

  it("redirects protected pages to /login", async () => {
    for (const p of ["/", "/trends", "/day/2026-01-01"]) {
      const res = await proxy(new NextRequest(`http://localhost${p}`));
      expect(res.status, p).toBe(307);
      const loc = new URL(res.headers.get("location")!);
      expect(loc.pathname).toBe("/login");
      expect(loc.searchParams.get("next")).toBe(p);
    }
  });

  it("keeps /login and /auth/* reachable", async () => {
    for (const p of ["/login", "/auth/callback"]) {
      const res = await proxy(new NextRequest(`http://localhost${p}`));
      expect(res.status, p).toBe(200);
    }
  });
});

describe("/auth/callback: cache headers", () => {
  it("applies headers to the success redirect", async () => {
    writes = [CACHE];
    const res = await callback(
      new Request("http://localhost/auth/callback?code=abc&next=/trends"),
    );
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toBe("http://localhost/trends");
    expectCacheHeaders(res);
    expect(cookieSet).toHaveBeenCalledWith("sb-x-auth-token", "v", {
      path: "/",
    });
  });

  it("applies headers on the token_hash (verifyOtp) path", async () => {
    writes = [CACHE];
    const res = await callback(
      new Request(
        "http://localhost/auth/callback?token_hash=t&type=magiclink",
      ),
    );
    expect(res.headers.get("location")).toBe("http://localhost/");
    expectCacheHeaders(res);
  });

  it("adds nothing when no cookies/headers were written", async () => {
    const res = await callback(
      new Request("http://localhost/auth/callback?code=abc"),
    );
    expect(res.headers.get("location")).toBe("http://localhost/");
    expectNoCacheHeaders(res);
  });

  it("exchange error still redirects to /login with the message", async () => {
    authError = { message: "expired" };
    const res = await callback(
      new Request("http://localhost/auth/callback?code=abc"),
    );
    const loc = new URL(res.headers.get("location")!);
    expect(loc.pathname).toBe("/login");
    expect(loc.searchParams.get("error")).toBe("expired");
  });

  it("missing token redirects to /login without touching supabase", async () => {
    const res = await callback(new Request("http://localhost/auth/callback"));
    expect(new URL(res.headers.get("location")!).pathname).toBe("/login");
    expect(cookieSet).not.toHaveBeenCalled();
  });
});

describe("/auth/callback: next open-redirect guard", () => {
  const hostile = [
    "@evil.com",
    "//evil.com",
    "/\\evil.com",
    "https://evil.com",
    "/%2F%2Fevil.com",
    "/\t/evil.com",
    "/\n/evil.com",
    " /evil.com",
    "javascript:alert(1)",
  ];

  for (const next of hostile) {
    it(`keeps redirect same-origin for next=${JSON.stringify(next)}`, async () => {
      const u = new URL("http://localhost/auth/callback?code=abc");
      u.searchParams.set("next", next);
      const res = await callback(new Request(u));
      const loc = new URL(res.headers.get("location")!);
      expect(loc.origin).toBe("http://localhost");
      expect(loc.host).toBe("localhost");
    });
  }

  it("raw (unencoded) hostile values also stay same-origin", async () => {
    for (const raw of ["@evil.com", "//evil.com", "/%2F%2Fevil.com"]) {
      const res = await callback(
        new Request(`http://localhost/auth/callback?code=abc&next=${raw}`),
      );
      expect(new URL(res.headers.get("location")!).origin).toBe(
        "http://localhost",
      );
    }
  });

  it("allows a normal path (with query)", async () => {
    const res = await callback(
      new Request(
        "http://localhost/auth/callback?code=abc&next=" +
          encodeURIComponent("/day/2026-01-01?x=1"),
      ),
    );
    expect(res.headers.get("location")).toBe(
      "http://localhost/day/2026-01-01?x=1",
    );
  });

  it("safeNextUrl unit cases", () => {
    const o = "https://app.example";
    expect(safeNextUrl("/trends", o).href).toBe("https://app.example/trends");
    expect(safeNextUrl(null, o).href).toBe("https://app.example/");
    expect(safeNextUrl("", o).href).toBe("https://app.example/");
    expect(safeNextUrl("@evil.com", o).href).toBe("https://app.example/");
    expect(safeNextUrl("//evil.com", o).href).toBe("https://app.example/");
    expect(safeNextUrl("/\\evil.com", o).href).toBe("https://app.example/");
    expect(safeNextUrl("https://evil.com", o).href).toBe(
      "https://app.example/",
    );
    expect(safeNextUrl("/%2F%2Fevil.com", o).origin).toBe(o);
  });
});
