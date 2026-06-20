import { describe, expect, it } from "vitest";
import { AGP_WINDOWS, clampAgpDays, shapeAgpHours } from "@/lib/agp";

describe("clampAgpDays", () => {
  it("passes allowed windows through", () => {
    for (const d of AGP_WINDOWS) {
      expect(clampAgpDays(d)).toBe(d);
    }
  });

  it("falls back to 30 for anything else", () => {
    expect(clampAgpDays(7)).toBe(30);
    expect(clampAgpDays(0)).toBe(30);
    expect(clampAgpDays(-5)).toBe(30);
    expect(clampAgpDays(365)).toBe(30);
    expect(clampAgpDays(Number.NaN)).toBe(30);
  });
});

describe("shapeAgpHours", () => {
  it("coerces pg string numerics to numbers", () => {
    const out = shapeAgpHours([
      {
        hour: "6",
        p05: "81.5",
        p25: "95",
        p50: "110",
        p75: "150.25",
        p95: "201",
        n: "1234",
      },
    ]);
    expect(out).toEqual([
      { hour: 6, p05: 81.5, p25: 95, p50: 110, p75: 150.25, p95: 201, n: 1234 },
    ]);
  });

  it("preserves NULL percentiles for hours without readings", () => {
    const out = shapeAgpHours([
      { hour: 3, p05: null, p25: null, p50: null, p75: null, p95: null, n: 0 },
    ]);
    expect(out).toEqual([
      { hour: 3, p05: null, p25: null, p50: null, p75: null, p95: null, n: 0 },
    ]);
  });

  it("returns an empty array for no rows", () => {
    expect(shapeAgpHours([])).toEqual([]);
  });
});
