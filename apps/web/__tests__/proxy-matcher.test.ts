import { describe, expect, it } from "vitest";
import type { NextRequest } from "next/server";
import { proxy, shouldBypassProxy } from "@/proxy";

describe("proxy matcher", () => {
  it("bypasses api routes", () => {
    expect(shouldBypassProxy("/api/meal_rise_cron")).toBe(true);
    expect(shouldBypassProxy("/api/cron/meal-rise")).toBe(true);
  });

  it("does not bypass page routes", () => {
    expect(shouldBypassProxy("/dashboard")).toBe(false);
    expect(shouldBypassProxy("/login")).toBe(false);
  });

  it("returns pass-through response for bypassed api routes", async () => {
    const req = {
      nextUrl: { pathname: "/api/meal_rise_cron" },
    } as unknown as NextRequest;
    const res = await proxy(req);
    expect(res.status).toBe(200);
  });
});
