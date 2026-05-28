import { describe, expect, it } from "vitest";
import { verifyCronAuth } from "@/lib/cron/auth";

describe("verifyCronAuth", () => {
  const original = process.env.CRON_SECRET;

  it("rejects missing authorization header", () => {
    process.env.CRON_SECRET = "test-secret";
    const req = new Request("http://localhost/api/cron/meal-rise");
    expect(verifyCronAuth(req)).toBe(false);
  });

  it("rejects wrong bearer token", () => {
    process.env.CRON_SECRET = "test-secret";
    const req = new Request("http://localhost/api/cron/meal-rise", {
      headers: { authorization: "Bearer wrong" },
    });
    expect(verifyCronAuth(req)).toBe(false);
  });

  it("accepts matching bearer token", () => {
    process.env.CRON_SECRET = "test-secret";
    const req = new Request("http://localhost/api/cron/meal-rise", {
      headers: { authorization: "Bearer test-secret" },
    });
    expect(verifyCronAuth(req)).toBe(true);
  });

  it("rejects when CRON_SECRET is unset", () => {
    delete process.env.CRON_SECRET;
    const req = new Request("http://localhost/api/cron/meal-rise", {
      headers: { authorization: "Bearer test-secret" },
    });
    expect(verifyCronAuth(req)).toBe(false);
    process.env.CRON_SECRET = original;
  });
});
