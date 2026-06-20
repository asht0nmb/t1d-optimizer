/**
 * Integrate commanded basal rate (u/hr) x duration (hours) over a day.
 *
 * Source of truth: detection/features.py::_integrate_basal. Each in-window
 * row spans [this_ts, min(next_ts, day_end)); the final row extends to
 * day_end. Tandem basal rows are event-driven (emitted on rate changes), so
 * a fixed 5-minute cadence assumption (rate * 5/60) is wrong whenever a rate
 * persists. Rows must be pre-sorted ascending and already filtered to the day.
 */
export function integrateBasalUnits(
  rows: { timestamp: Date; rate: number }[],
  dayEnd: Date,
): number {
  let total = 0;
  for (let i = 0; i < rows.length; i++) {
    const start = rows[i].timestamp.getTime();
    const next = i + 1 < rows.length ? rows[i + 1].timestamp.getTime() : dayEnd.getTime();
    const end = Math.min(next, dayEnd.getTime());
    const durHours = (end - start) / 3_600_000;
    if (durHours > 0) total += rows[i].rate * durHours;
  }
  return total;
}
