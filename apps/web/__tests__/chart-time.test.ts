import { describe, expect, it } from "vitest";
import {
  alignCompareSeries,
  DAY_MINUTES,
  formatMinutesLabel,
  hourTicks,
  minutesSinceMidnight,
} from "@/lib/chart-time";

// Fixed-offset ISO strings so the test is timezone-stable: the local Date
// fields are exercised via explicit offsets back to wall-clock equivalents.
function iso(h: number, m: number): string {
  // Build a local-time Date then serialize — keeps minutesSinceMidnight stable
  // regardless of the runner's TZ.
  const d = new Date(2026, 0, 1, h, m, 0);
  return d.toISOString();
}

describe("minutesSinceMidnight", () => {
  it("maps midnight to 0", () => {
    expect(minutesSinceMidnight(iso(0, 0))).toBe(0);
  });

  it("maps 06:30 to 390", () => {
    expect(minutesSinceMidnight(iso(6, 30))).toBe(390);
  });

  it("maps 23:59 to 1439", () => {
    expect(minutesSinceMidnight(iso(23, 59))).toBe(1439);
  });
});

describe("formatMinutesLabel", () => {
  it("formats whole hours", () => {
    expect(formatMinutesLabel(0)).toBe("00:00");
    expect(formatMinutesLabel(390)).toBe("06:30");
    expect(formatMinutesLabel(DAY_MINUTES)).toBe("00:00"); // wraps
  });
});

describe("hourTicks", () => {
  it("returns inclusive ticks every 3h by default", () => {
    expect(hourTicks()).toEqual([0, 180, 360, 540, 720, 900, 1080, 1260, 1440]);
  });
});

describe("alignCompareSeries", () => {
  it("aligns by time-of-day, not array index, when counts differ", () => {
    // series_a is sparse (2 readings); series_b is dense (3 readings).
    const a = [
      { timestamp: iso(8, 0), bg_mgdl: 100 },
      { timestamp: iso(12, 0), bg_mgdl: 150 },
    ];
    const b = [
      { timestamp: iso(8, 0), bg_mgdl: 90 },
      { timestamp: iso(10, 0), bg_mgdl: 110 },
      { timestamp: iso(12, 0), bg_mgdl: 200 },
    ];
    const rows = alignCompareSeries(a, b);

    // Three distinct minute buckets: 480 (08:00), 600 (10:00), 720 (12:00).
    expect(rows.map((r) => r.minute)).toEqual([480, 600, 720]);

    // 08:00 has both days; values are NOT swapped by index.
    expect(rows[0]).toEqual({ minute: 480, a: 100, b: 90 });
    // 10:00 only has b — a must be null (positional zip would have mismatched).
    expect(rows[1]).toEqual({ minute: 600, a: null, b: 110 });
    // 12:00 has both.
    expect(rows[2]).toEqual({ minute: 720, a: 150, b: 200 });
  });

  it("sorts by minute regardless of input order", () => {
    const a = [
      { timestamp: iso(20, 0), bg_mgdl: 1 },
      { timestamp: iso(4, 0), bg_mgdl: 2 },
    ];
    const rows = alignCompareSeries(a, []);
    expect(rows.map((r) => r.minute)).toEqual([240, 1200]);
  });

  it("returns empty array for two empty series", () => {
    expect(alignCompareSeries([], [])).toEqual([]);
  });
});
