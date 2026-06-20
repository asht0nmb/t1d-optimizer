/**
 * Pure time-axis helpers shared by the chart components.
 *
 * Charts must place readings on a *numeric* x-axis (minutes since local
 * midnight, or epoch ms) so that gaps are proportional and multiple panels /
 * series line up. Keying on "HH:mm" category strings desyncs panels and
 * positional array-index merges desync days with different reading counts.
 */

const MINUTES_PER_DAY = 24 * 60;

/**
 * Minutes since local midnight for an ISO timestamp, in [0, 1440).
 * Uses the *local* wall-clock fields of the parsed Date, matching the
 * day window the API already localizes into.
 */
export function minutesSinceMidnight(iso: string): number {
  const d = new Date(iso);
  return d.getHours() * 60 + d.getMinutes() + d.getSeconds() / 60;
}

/** Epoch milliseconds for an ISO timestamp. */
export function epochMs(iso: string): number {
  return new Date(iso).getTime();
}

/** "HH:mm" label for a minutes-since-midnight value (wraps at 1440). */
export function formatMinutesLabel(minutes: number): string {
  const m = ((Math.round(minutes) % MINUTES_PER_DAY) + MINUTES_PER_DAY) %
    MINUTES_PER_DAY;
  const hh = Math.floor(m / 60);
  const mm = m % 60;
  return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
}

/** Even tick values (minutes) across a full day, every `stepHours`. */
export function hourTicks(stepHours = 3): number[] {
  const ticks: number[] = [];
  for (let h = 0; h <= 24; h += stepHours) ticks.push(h * 60);
  return ticks;
}

export const DAY_MINUTES = MINUTES_PER_DAY;

interface TimedPoint {
  timestamp: string;
  bg_mgdl: number;
}

export interface CompareAlignedRow {
  minute: number;
  a: number | null;
  b: number | null;
}

/**
 * Project two days' CGM series onto a single shared minutes-since-midnight
 * x-axis. Each reading keeps its own minute coordinate (no positional zip),
 * so days with different reading counts stay aligned by time-of-day.
 *
 * Rows are sorted by minute; each row carries whichever of a/b fall on that
 * exact minute (others null). Recharts `connectNulls` then draws each series
 * as a continuous line against the numeric axis.
 */
export function alignCompareSeries(
  seriesA: readonly TimedPoint[],
  seriesB: readonly TimedPoint[],
): CompareAlignedRow[] {
  const byMinute = new Map<number, CompareAlignedRow>();

  const upsert = (minute: number, key: "a" | "b", value: number) => {
    const existing = byMinute.get(minute);
    if (existing) {
      existing[key] = value;
    } else {
      byMinute.set(minute, { minute, a: null, b: null, [key]: value });
    }
  };

  for (const p of seriesA) upsert(minutesSinceMidnight(p.timestamp), "a", p.bg_mgdl);
  for (const p of seriesB) upsert(minutesSinceMidnight(p.timestamp), "b", p.bg_mgdl);

  return Array.from(byMinute.values()).sort((x, y) => x.minute - y.minute);
}
