/**
 * Pure freshness classifier — no pg imports, safe to import in vitest.
 *
 * Given an ISO timestamp and a threshold in hours, returns:
 *   "ok"      — timestamp is within threshold hours of referenceNow
 *   "stale"   — timestamp exists but is older than threshold hours
 *   "missing" — timestamp is null or undefined
 *
 * A timestamp more than FUTURE_SKEW_MINUTES ahead of referenceNow is clock
 * skew or bad data, so it is NOT reported "ok"; it maps to "stale" (the
 * warning badge). Callers must pass an offset-carrying instant (e.g. UTC
 * "...Z"), not a naive local-time string, or the comparison is meaningless.
 */
export const FUTURE_SKEW_MINUTES = 5;

export type FreshnessStatus = "ok" | "stale" | "missing";

export function classifyFreshness(
  timestamp: string | null | undefined,
  thresholdHours: number,
  referenceNow?: string,
): FreshnessStatus {
  if (timestamp == null) return "missing";
  const ref = referenceNow ? new Date(referenceNow).getTime() : Date.now();
  const ts = new Date(timestamp).getTime();
  const ageHours = (ref - ts) / (1000 * 60 * 60);
  if (ageHours < -FUTURE_SKEW_MINUTES / 60) return "stale";
  return ageHours <= thresholdHours ? "ok" : "stale";
}
