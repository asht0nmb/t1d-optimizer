import { beforeEach, describe, expect, it, vi } from "vitest";
import fs from "node:fs";
import path from "node:path";

/**
 * Behavioral proof of the auth model, independent of Next.js internals:
 *  - the proxy (formerly "middleware") redirects unauthenticated page
 *    requests to /login and lets signed-in users through;
 *  - every /api route handler returns 401 with no session / no bearer token,
 *    and never touches the data layer when it does.
 */

const getUser = vi.fn();
const createServiceClient = vi.fn(() => {
  throw new Error("data layer must not be reached when unauthenticated");
});
vi.mock("@/lib/supabase/server", () => ({
  createSessionClient: async () => ({ auth: { getUser } }),
  createServiceClient: () => createServiceClient(),
}));

vi.mock("@supabase/ssr", () => ({
  createServerClient: () => ({ auth: { getUser } }),
}));

import { NextRequest } from "next/server";
import { config, proxy } from "@/proxy";

function collectRoutes(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) collectRoutes(p, out);
    else if (entry.name === "route.ts") out.push(p);
  }
  return out;
}

const apiDir = path.join(__dirname, "..", "app", "api");

describe("proxy: page requests", () => {
  beforeEach(() => {
    getUser.mockReset();
    process.env.NEXT_PUBLIC_SUPABASE_URL = "http://localhost";
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = "placeholder-anon-key";
  });

  it("redirects an unauthenticated page request to /login with next=", async () => {
    getUser.mockResolvedValue({ data: { user: null } });
    const res = await proxy(new NextRequest("http://localhost/trends"));
    expect(res.status).toBe(307);
    const loc = new URL(res.headers.get("location")!);
    expect(loc.pathname).toBe("/login");
    expect(loc.searchParams.get("next")).toBe("/trends");
  });

  it("redirects the root path when unauthenticated", async () => {
    getUser.mockResolvedValue({ data: { user: null } });
    const res = await proxy(new NextRequest("http://localhost/"));
    expect(res.status).toBe(307);
    expect(new URL(res.headers.get("location")!).pathname).toBe("/login");
  });

  it("allows /login and /auth/* without a session", async () => {
    getUser.mockResolvedValue({ data: { user: null } });
    for (const p of ["/login", "/auth/callback"]) {
      const res = await proxy(new NextRequest(`http://localhost${p}`));
      expect(res.status).toBe(200);
    }
  });

  it("lets a signed-in user through and bounces them off /login", async () => {
    getUser.mockResolvedValue({ data: { user: { id: "u1" } } });
    const ok = await proxy(new NextRequest("http://localhost/trends"));
    expect(ok.status).toBe(200);
    const login = await proxy(new NextRequest("http://localhost/login"));
    expect(login.status).toBe(307);
    expect(new URL(login.headers.get("location")!).pathname).toBe("/");
  });

  it("matcher covers pages but not api routes or static assets", () => {
    const re = new RegExp(`^${config.matcher[0]}$`);
    for (const p of ["/", "/day/2026-01-01", "/trends", "/login", "/status"]) {
      expect(re.test(p), p).toBe(true);
    }
    for (const p of [
      "/api/status",
      "/_next/static/a.js",
      "/favicon.ico",
      "/x.png",
    ]) {
      expect(re.test(p), p).toBe(false);
    }
  });
});

describe("api routes: unauthenticated requests are rejected", () => {
  beforeEach(() => {
    getUser.mockReset();
    createServiceClient.mockClear();
    delete process.env.CRON_SECRET;
  });

  const routes = collectRoutes(apiDir);

  it("discovers the expected number of routes", () => {
    expect(routes.length).toBeGreaterThanOrEqual(12);
  });

  for (const file of routes) {
    const rel = path.relative(apiDir, file);
    it(`/api/${path.dirname(rel)} -> 401 with no session`, async () => {
      getUser.mockResolvedValue({ data: { user: null } });
      const mod = await import(/* @vite-ignore */ file);
      const handlers = ["GET", "POST", "PUT", "PATCH", "DELETE"].filter(
        (m) => typeof mod[m] === "function",
      );
      expect(handlers.length).toBeGreaterThan(0);
      for (const m of handlers) {
        const res: Response = await mod[m](
          new Request(
            "http://localhost/api/x?a=2026-01-01&b=2026-01-02&from=2026-01-01&to=2026-01-02",
          ),
          { params: Promise.resolve({ date: "2026-01-01" }) },
        );
        expect(res.status, `${m} ${rel}`).toBe(401);
      }
      expect(createServiceClient).not.toHaveBeenCalled();
    });
  }

  it("rejects a wrong cron bearer token", async () => {
    process.env.CRON_SECRET = "s3cret";
    const mod = await import("@/app/api/cron/meal-rise/route");
    const res = await mod.GET(
      new Request("http://localhost/api/cron/meal-rise", {
        headers: { authorization: "Bearer wrong" },
      }),
    );
    expect(res.status).toBe(401);
  });

  it("day route awaits async params when signed in (invalid date -> 400)", async () => {
    getUser.mockResolvedValue({ data: { user: { id: "u1" } } });
    const mod = await import("@/app/api/day/[date]/route");
    const res = await mod.GET(new Request("http://localhost/api/day/nope"), {
      params: Promise.resolve({ date: "nope" }),
    });
    expect(res.status).toBe(400);
  });
});
