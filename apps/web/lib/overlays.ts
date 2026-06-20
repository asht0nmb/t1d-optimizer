/** Pure interval helpers for day-view overlay shading (CGM gaps, site issues). */

export interface IsoInterval {
  start: string;
  end: string;
}

/** Clip [start, end) to [windowStart, windowEnd); null when there is no overlap. */
export function clipIntervalToWindow(
  start: string,
  end: string,
  windowStart: string,
  windowEnd: string,
): IsoInterval | null {
  const s = Math.max(Date.parse(start), Date.parse(windowStart));
  const e = Math.min(Date.parse(end), Date.parse(windowEnd));
  if (!Number.isFinite(s) || !Number.isFinite(e) || e <= s) return null;
  return { start: new Date(s).toISOString(), end: new Date(e).toISOString() };
}

/**
 * Snap an interval to bracketing entries of an ascending timestamp list so a
 * recharts ReferenceArea can target existing category-axis values:
 * x1 = last timestamp <= interval.start (else the first timestamp),
 * x2 = first timestamp >= interval.end (else the last timestamp).
 * Null when the snapped area would be empty (no data, or zero width).
 */
export function snapIntervalToTimestamps(
  interval: IsoInterval,
  timestamps: readonly string[],
): { x1: string; x2: string } | null {
  if (timestamps.length === 0) return null;
  const startMs = Date.parse(interval.start);
  const endMs = Date.parse(interval.end);

  let x1 = timestamps[0];
  for (const t of timestamps) {
    if (Date.parse(t) <= startMs) x1 = t;
    else break;
  }

  let x2 = timestamps[timestamps.length - 1];
  for (let i = timestamps.length - 1; i >= 0; i--) {
    if (Date.parse(timestamps[i]) >= endMs) x2 = timestamps[i];
    else break;
  }

  if (Date.parse(x2) <= Date.parse(x1)) return null;
  return { x1, x2 };
}
