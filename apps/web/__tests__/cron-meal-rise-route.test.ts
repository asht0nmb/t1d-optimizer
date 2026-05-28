import { afterEach, describe, expect, it } from "vitest";
import { GET } from "@/app/api/cron/meal-rise/route";

describe("GET /api/cron/meal-rise", () => {
  const originalSecret = process.env.CRON_SECRET;

  afterEach(() => {
    if (originalSecret === undefined) {
      delete process.env.CRON_SECRET;
    } else {
      process.env.CRON_SECRET = originalSecret;
    }
  });

  it("returns 401 for missing auth", async () => {
    process.env.CRON_SECRET = "test-secret";
    const request = new Request("http://localhost/api/cron/meal-rise");
    const response = await GET(request);
    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({ error: "unauthorized" });
  });

  it("returns health payload for valid auth", async () => {
    process.env.CRON_SECRET = "test-secret";
    const request = new Request("http://localhost/api/cron/meal-rise", {
      headers: { authorization: "Bearer test-secret" },
    });
    const response = await GET(request);
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      ok: true,
      mode: "health_only",
      scheduler: "external",
      message: "Meal-rise cron execution runs outside the Next.js web deployment.",
    });
  });
});
