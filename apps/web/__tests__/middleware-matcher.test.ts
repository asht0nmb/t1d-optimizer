import { describe, expect, it } from "vitest";
import { middleware, shouldBypassMiddleware } from "@/middleware";

describe("middleware matcher", () => {
  it("bypasses api routes", () => {
    expect(shouldBypassMiddleware("/api/meal_rise_cron")).toBe(true);
    expect(shouldBypassMiddleware("/api/cron/meal-rise")).toBe(true);
  });

  it("does not bypass page routes", () => {
    expect(shouldBypassMiddleware("/dashboard")).toBe(false);
    expect(shouldBypassMiddleware("/login")).toBe(false);
  });

  it("returns pass-through response for bypassed api routes", async () => {
    const req = {
      nextUrl: { pathname: "/api/meal_rise_cron" },
    } as any;
    const res = await middleware(req);
    expect(res.status).toBe(200);
  });
});
