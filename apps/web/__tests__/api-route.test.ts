import { describe, expect, it } from "vitest";
import { jsonError, jsonOk, parseIntParam } from "@/lib/api/route";

describe("api route helpers", () => {
  it("jsonOk wraps data", async () => {
    const res = jsonOk({ ok: true });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
  });

  it("jsonError sets status", async () => {
    const res = jsonError("nope", 400);
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "nope" });
  });

  it("parseIntParam falls back", () => {
    expect(parseIntParam(null, 14)).toBe(14);
    expect(parseIntParam("7", 14)).toBe(7);
    expect(parseIntParam("x", 14)).toBe(14);
  });
});
