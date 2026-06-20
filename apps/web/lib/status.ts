/**
 * Pure freshness classifier — no pg imports, safe to import in vitest.
 *
 * Given an ISO timestamp and a threshold in hours, returns:
 *   "ok"      — timestamp is within threshold hours of referenceNow
 *   "stale"   — timestamp exists but is older than threshold hours
 *   "missing" — timestamp is null or undefined
 */
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
  return ageHours <= thresholdHours ? "ok" : "stale";
}
