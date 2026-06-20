import type { CgmReportResponse } from "@/lib/types/api";

/**
 * Pure shaping helpers for the clinical report page. No React, no fetch — kept
 * here so the formatting and the band-segment math are unit-testable in
 * isolation. The numeric formulas all live in Python (core.metrics); this file
 * only formats and arranges already-computed values for display.
 */

/** Band palette — mirrors apps/local/charts/report.py (BG day-view palette). */
export const bandColors = {
  tbr2: "#B71C1C", // very low
  tbr1: "#E53935", // low
  tir: "#2E7D32", // in range
  tar1: "#FB8C00", // high
  tar2: "#E65100", // very high
} as const;

export interface BandSegment {
  key: "tbr2" | "tbr1" | "tir" | "tar1" | "tar2";
  label: string;
  pct: number;
  color: string;
}

const BAND_DEFS: ReadonlyArray<Pick<BandSegment, "key" | "label">> = [
  { key: "tbr2", label: "Very low" },
  { key: "tbr1", label: "Low" },
  { key: "tir", label: "In range" },
  { key: "tar1", label: "High" },
  { key: "tar2", label: "Very high" },
];

/**
 * Build the five stacked time-in-bands segments (tbr2→tar2) from a report.
 * Negative or non-finite values are clamped to 0 so the bar never renders a
 * bogus segment; the order matches the clinical low→high convention.
 */
export function buildBandSegments(report: CgmReportResponse): BandSegment[] {
  return BAND_DEFS.map(({ key, label }) => {
    const raw = report[key];
    const pct = Number.isFinite(raw) && raw > 0 ? raw : 0;
    return { key, label, pct, color: bandColors[key] };
  });
}

/**
 * Format a nullable metric for a tile. `null` → the em-dash placeholder.
 * `digits` controls decimal places; `suffix` is appended (e.g. "%", " mg/dL").
 */
export function formatMetric(
  value: number | null | undefined,
  opts: { digits?: number; suffix?: string } = {},
): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  const { digits = 1, suffix = "" } = opts;
  return `${value.toFixed(digits)}${suffix}`;
}

/** Sufficiency note shown when the window is below the consensus gate. */
export function sufficiencyNote(report: CgmReportResponse): string | null {
  if (report.meets_sufficiency) return null;
  return (
    `Data sufficiency not met (need ≥14 days and ≥70% active CGM time; ` +
    `have ${report.days_covered} days, ${report.active_pct.toFixed(0)}% active). ` +
    `GMI and GRI are withheld until the window is sufficient.`
  );
}
