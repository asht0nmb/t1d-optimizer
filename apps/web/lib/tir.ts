import type { BgTargets } from "@/lib/types/api";

export interface TirBreakdown {
  in_range_pct: number;
  below_pct: number;
  above_pct: number;
  tir_pct: number;
  reading_count: number;
}

/** Time-in-range breakdown for a list of BG values (mg/dL). */
export function computeTirBreakdown(
  readings: number[],
  targets: BgTargets,
): TirBreakdown {
  const n = readings.length;
  if (n === 0) {
    return {
      in_range_pct: 0,
      below_pct: 0,
      above_pct: 0,
      tir_pct: 0,
      reading_count: 0,
    };
  }
  let below = 0;
  let inRange = 0;
  let above = 0;
  for (const bg of readings) {
    if (bg < targets.low) below += 1;
    else if (bg > targets.high) above += 1;
    else inRange += 1;
  }
  const pct = (x: number) => (x / n) * 100;
  return {
    in_range_pct: pct(inRange),
    below_pct: pct(below),
    above_pct: pct(above),
    tir_pct: pct(inRange),
    reading_count: n,
  };
}
