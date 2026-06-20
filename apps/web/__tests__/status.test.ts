import { describe, expect, it } from "vitest";
import { classifyFreshness } from "@/lib/status";

describe("classifyFreshness", () => {
  const now = "2026-06-11T12:00:00.000Z";

  it("returns 'missing' when timestamp is null", () => {
    expect(classifyFreshness(null, 26, now)).toBe("missing");
  });

  it("returns 'missing' when timestamp is undefined", () => {
    expect(classifyFreshness(undefined, 26, now)).toBe("missing");
  });

  it("returns 'ok' when timestamp is within threshold", () => {
    // 25 hours ago — under 26h threshold
    const ts = "2026-06-10T11:00:00.000Z";
    expect(classifyFreshness(ts, 26, now)).toBe("ok");
  });

  it("returns 'stale' when timestamp is beyond threshold", () => {
    // 27 hours ago — over 26h threshold
    const ts = "2026-06-10T09:00:00.000Z";
    expect(classifyFreshness(ts, 26, now)).toBe("stale");
  });

  it("returns 'ok' when timestamp equals threshold exactly", () => {
    // exactly 26 hours ago
    const ts = "2026-06-10T10:00:00.000Z";
    expect(classifyFreshness(ts, 26, now)).toBe("ok");
  });

  it("uses Date.now() when referenceNow is omitted", () => {
    // A very recent timestamp should always be ok
    const ts = new Date(Date.now() - 1000).toISOString();
    expect(classifyFreshness(ts, 26)).toBe("ok");
  });

  it("uses different thresholds correctly (24h detection threshold)", () => {
    // 23 hours ago — under 24h
    const ts = "2026-06-10T13:00:00.000Z";
    expect(classifyFreshness(ts, 24, now)).toBe("ok");
    // 25 hours ago — over 24h
    const ts2 = "2026-06-10T11:00:00.000Z";
    expect(classifyFreshness(ts2, 24, now)).toBe("stale");
  });
});
