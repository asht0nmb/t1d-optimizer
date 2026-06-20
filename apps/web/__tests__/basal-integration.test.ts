import { describe, expect, it } from "vitest";
import { integrateBasalUnits } from "@/lib/basal";

// Mirrors detection/features.py::_integrate_basal — integrate
// commanded_rate (u/hr) x duration (hours), where each row spans
// [this_ts, min(next_ts, day_end)) and the final row extends to day_end.
describe("integrateBasalUnits", () => {
  it("integrates by true inter-row duration, not a fixed 5-min cadence", () => {
    // Single 1.0 u/hr row at 00:00 persisting until day_end (24h) = 24 units.
    // The old `rate * 5/60` assumption would have returned 1/12 = 0.083.
    const dayStart = new Date("2026-03-01T00:00:00Z");
    const dayEnd = new Date("2026-03-02T00:00:00Z");
    const units = integrateBasalUnits(
      [{ timestamp: new Date("2026-03-01T00:00:00Z"), rate: 1.0 }],
      dayEnd,
    );
    expect(units).toBeCloseTo(24, 6);
    expect(dayStart).toBeInstanceOf(Date);
  });

  it("sums rate x duration across multiple rows, last row to day_end", () => {
    // 0.5 u/hr for [00:00, 06:00) = 3.0; 1.0 u/hr for [06:00, 24:00) = 18.0.
    const dayEnd = new Date("2026-03-02T00:00:00Z");
    const units = integrateBasalUnits(
      [
        { timestamp: new Date("2026-03-01T00:00:00Z"), rate: 0.5 },
        { timestamp: new Date("2026-03-01T06:00:00Z"), rate: 1.0 },
      ],
      dayEnd,
    );
    expect(units).toBeCloseTo(21, 6);
  });

  it("clips the final row at day_end even if a later row exists conceptually", () => {
    // Last row at 23:00 with rate 2.0 -> [23:00, 24:00) = 2.0 units.
    const dayEnd = new Date("2026-03-02T00:00:00Z");
    const units = integrateBasalUnits(
      [{ timestamp: new Date("2026-03-01T23:00:00Z"), rate: 2.0 }],
      dayEnd,
    );
    expect(units).toBeCloseTo(2, 6);
  });

  it("returns 0 for no rows", () => {
    expect(
      integrateBasalUnits([], new Date("2026-03-02T00:00:00Z")),
    ).toBe(0);
  });
});
