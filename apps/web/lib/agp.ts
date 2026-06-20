/** Pure AGP helpers (window clamping + response shaping) — no pg imports so vitest can load them. */
import type { AgpHourPoint } from "@/lib/types/api";

export const AGP_WINDOWS = [14, 30, 90] as const;
export type AgpWindow = (typeof AGP_WINDOWS)[number];

export function clampAgpDays(days: number): AgpWindow {
  return (AGP_WINDOWS as readonly number[]).includes(days)
    ? (days as AgpWindow)
    : 30;
}

/** Raw pg row: numerics may arrive as strings; empty hours carry NULL percentiles. */
export interface RawAgpRow {
  hour: number | string;
  p05: number | string | null;
  p25: number | string | null;
  p50: number | string | null;
  p75: number | string | null;
  p95: number | string | null;
  n: number | string;
}

function toNumberOrNull(value: number | string | null): number | null {
  return value == null ? null : Number(value);
}

export function shapeAgpHours(rows: RawAgpRow[]): AgpHourPoint[] {
  return rows.map((r) => ({
    hour: Number(r.hour),
    p05: toNumberOrNull(r.p05),
    p25: toNumberOrNull(r.p25),
    p50: toNumberOrNull(r.p50),
    p75: toNumberOrNull(r.p75),
    p95: toNumberOrNull(r.p95),
    n: Number(r.n),
  }));
}
