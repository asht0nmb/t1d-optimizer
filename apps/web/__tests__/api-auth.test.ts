import { beforeEach, describe, expect, it, vi } from "vitest";

const getUser = vi.fn();
vi.mock("@/lib/supabase/server", () => ({
  createSessionClient: async () => ({ auth: { getUser } }),
}));

import { requireSession } from "@/lib/api/auth";

describe("requireSession", () => {
  beforeEach(() => getUser.mockReset());

  it("returns null when a user session exists", async () => {
    getUser.mockResolvedValue({ data: { user: { id: "u1" } } });
    expect(await requireSession()).toBeNull();
  });

  it("returns 401 when there is no user", async () => {
    getUser.mockResolvedValue({ data: { user: null } });
    const res = await requireSession();
    expect(res).not.toBeNull();
    expect(res!.status).toBe(401);
  });

  it("fails closed (401) when the auth check errors", async () => {
    // Resolving undefined makes the `{ data: { user } }` destructuring in
    // requireSession throw — exercising the catch-and-401 path. (A spy that
    // throws directly trips vitest 4's unhandled-error attribution even
    // when the error is caught, so the error is induced inside the
    // function under test instead.)
    getUser.mockResolvedValue(undefined);
    const res = await requireSession();
    expect(res!.status).toBe(401);
  });

  it("guards every API route with requireSession or verifyCronAuth", async () => {
    // Backstop: every app/api/**/route.ts must reference a session guard
    // (requireSession) or the cron bearer guard (verifyCronAuth). This blocks
    // a future route from shipping unauthenticated.
    const fs = await import("node:fs");
    const path = await import("node:path");
    const apiDir = path.join(__dirname, "..", "app", "api");
    const routes: string[] = [];
    const walk = (dir: string) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const p = path.join(dir, entry.name);
        if (entry.isDirectory()) walk(p);
        else if (entry.name === "route.ts") routes.push(p);
      }
    };
    walk(apiDir);
    // Sanity: the scan actually found routes.
    expect(routes.length).toBeGreaterThan(0);
    const unguarded = routes.filter((p) => {
      const src = fs.readFileSync(p, "utf8");
      return !src.includes("requireSession") && !src.includes("verifyCronAuth");
    });
    expect(unguarded).toEqual([]);
  });
});
