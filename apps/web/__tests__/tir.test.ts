import { describe, expect, it } from "vitest";
import { computeTirBreakdown } from "@/lib/tir";

const targets = { low: 70, high: 180, target: 110 };

describe("computeTirBreakdown", () => {
  it("returns zeros for empty readings", () => {
    expect(computeTirBreakdown([], targets)).toEqual({
      in_range_pct: 0,
      below_pct: 0,
      above_pct: 0,
      tir_pct: 0,
      reading_count: 0,
    });
  });

  it("computes band percentages", () => {
    const readings = [60, 100, 200];
    const r = computeTirBreakdown(readings, targets);
    expect(r.reading_count).toBe(3);
    expect(r.below_pct).toBeCloseTo(33.33, 1);
    expect(r.in_range_pct).toBeCloseTo(33.33, 1);
    expect(r.above_pct).toBeCloseTo(33.33, 1);
    expect(r.tir_pct).toBe(r.in_range_pct);
  });
});
