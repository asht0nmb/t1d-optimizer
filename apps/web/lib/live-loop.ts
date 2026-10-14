/**
 * Pure mapping from the `fetch_state` row `source_id='live_cron'` to the two
 * live-loop status signals. No pg imports — safe to import in vitest.
 *
 * Semantics (cron worker, `apps/personal/cron/live_health.py`):
 *   - `meta.last_cycle_at`  advances every completed cycle  -> worker liveness
 *   - `last_synced_at`      advances only when FRESH CGM was evaluated
 *                                                            -> CGM flowing
 *   - `meta.last_outcome`, `meta.last_reading_age_min`, `meta.outcome_counts`
 *     (per local day) describe the latest cycle.
 * `meta` may be null, a legacy shape (`{"last_cursor": null}`) or malformed;
 * every access here is defensive.
 */
import { classifyFreshness } from "@/lib/status";
import type { StatusSignal } from "@/lib/types/api";

/** ~3 missed 5-min cycles. */
export const LIVE_THRESHOLD_HOURS = 0.25;

export interface LiveCronRow {
  /** Local-time text (`AT TIME ZONE tz`), for display. */
  last_synced_at: string | null;
  /** Same instant as UTC ISO ("...Z"), for age maths. */
  last_synced_at_utc: string | null;
  meta: unknown;
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return v !== null && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;
}

/** Valid, parseable timestamp string or null. */
function asTimestamp(v: unknown): string | null {
  return typeof v === "string" && !Number.isNaN(new Date(v).getTime())
    ? v
    : null;
}

function asFiniteNumber(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Instant -> "YYYY-MM-DD HH:MM" in `timezone` (matches pg `::text` slice). */
export function formatLocal(iso: string, timezone: string): string {
  try {
    return new Intl.DateTimeFormat("sv-SE", {
      timeZone: timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).format(new Date(iso));
  } catch {
    return iso.slice(0, 16).replace("T", " ");
  }
}

/**
 * Most recent day's outcome counts as "2026-10-14: no_candidate 140 · stale 3".
 *
 * Day keys are the worker's (`config.timezone` in the cron worker), while the
 * web timezone comes from `process.env.TZ` (default America/Los_Angeles); the
 * two are configured separately and can differ. So we never recompute
 * "today": we show the newest key as-is and label it with that date.
 */
export function formatOutcomeCounts(
  meta: Record<string, unknown> | null,
): string | null {
  const counts = asRecord(meta?.outcome_counts);
  if (!counts) return null;
  const day = Object.keys(counts)
    .filter((k) => /^\d{4}-\d{2}-\d{2}$/.test(k))
    .sort()
    .pop();
  if (!day) return null;
  const perDay = asRecord(counts[day]);
  if (!perDay) return null;
  const parts = Object.entries(perDay)
    .filter(([, n]) => typeof n === "number" && Number.isFinite(n) && n > 0)
    .sort((a, b) => (b[1] as number) - (a[1] as number))
    .map(([k, n]) => `${k} ${n}`);
  return parts.length > 0 ? `${day}: ${parts.join(" · ")}` : null;
}

export function buildLiveSignals(
  row: LiveCronRow | null,
  timezone: string,
  nowIso: string = new Date().toISOString(),
): StatusSignal[] {
  const meta = asRecord(row?.meta);

  // Worker liveness: meta.last_cycle_at, falling back to last_synced_at for
  // rows written before the observability change (no last_cycle_at).
  const cycleAt = asTimestamp(meta?.last_cycle_at);
  const workerTs = cycleAt ?? row?.last_synced_at_utc ?? null;
  const workerSignal: StatusSignal = {
    label: "Live loop (worker)",
    timestamp: workerTs ? formatLocal(workerTs, timezone) : null,
    freshness: classifyFreshness(workerTs, LIVE_THRESHOLD_HOURS, nowIso),
    detail: cycleAt ? "5-min poll" : "5-min poll (legacy heartbeat)",
  };

  // CGM flowing: last_synced_at only advances on fresh-CGM evaluations.
  const cgmTs = row?.last_synced_at_utc ?? null;
  const outcome =
    typeof meta?.last_outcome === "string" ? meta.last_outcome : null;
  const ageMin = asFiniteNumber(meta?.last_reading_age_min);
  const counts = formatOutcomeCounts(meta);
  const detail =
    [
      outcome ? `last: ${outcome}` : null,
      ageMin !== null ? `reading ${Math.round(ageMin)} min old` : null,
      counts ? `counts ${counts}` : null,
    ]
      .filter((p): p is string => p !== null)
      .join(" · ") || null;
  const cgmSignal: StatusSignal = {
    label: "Live CGM",
    timestamp: cgmTs ? formatLocal(cgmTs, timezone) : null,
    freshness: classifyFreshness(cgmTs, LIVE_THRESHOLD_HOURS, nowIso),
    detail,
  };

  return [workerSignal, cgmSignal];
}
