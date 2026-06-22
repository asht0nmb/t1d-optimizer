import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchJson } from "@/lib/fetch-json";

function mockFetch(impl: () => Promise<unknown>) {
  global.fetch = vi.fn(impl) as unknown as typeof fetch;
}

afterEach(() => vi.restoreAllMocks());

describe("fetchJson", () => {
  it("returns parsed JSON on a 2xx response", async () => {
    mockFetch(async () => ({ ok: true, status: 200, json: async () => ({ value: 1 }) }));
    const body = await fetchJson<{ value: number }>("/api/x");
    expect(body).toEqual({ value: 1 });
  });

  it("returns { error } (never throws 'Unexpected token') on a non-JSON 404 page", async () => {
    mockFetch(async () => ({
      ok: false,
      status: 404,
      json: async () => {
        throw new SyntaxError("Unexpected token '<', \"<!DOCTYPE \"... is not valid JSON");
      },
    }));
    const body = await fetchJson("/api/x");
    expect(body.error).toBeTruthy();
    expect(String(body.error)).not.toContain("Unexpected token");
  });

  it("surfaces the API's error message on a JSON error response", async () => {
    mockFetch(async () => ({ ok: false, status: 401, json: async () => ({ error: "unauthorized" }) }));
    const body = await fetchJson("/api/x");
    expect(body.error).toBe("unauthorized");
  });

  it("returns { error } on a network failure instead of throwing", async () => {
    mockFetch(async () => {
      throw new Error("network down");
    });
    const body = await fetchJson("/api/x");
    expect(String(body.error)).toContain("network down");
  });
});
