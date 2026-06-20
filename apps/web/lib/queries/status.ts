import { queryRows } from "@/lib/queries/db";
import { classifyFreshness } from "@/lib/status";
import type { StatusResponse, StatusSignal } from "@/lib/types/api";

// CGM/data freshness — nightly sync cadence + slack
const CGM_THRESHOLD_HOURS = 26;
// Detection recency — only fires on rises; absence isn't failure
const DETECTION_THRESHOLD_HOURS = 24;
// Live meal-rise loop heartbeat — the worker polls every 5 min and rewrites
// the `live_cron` fetch_state row each completed cycle. 15 min ≈ 3 missed
// cycles → stale. This is the only signal that distinguishes "loop healthy but
// idle" from "loop dead" (the detection signal cannot).
const LIVE_CRON_SOURCE = "live_cron";
const LIVE_CRON_THRESHOLD_HOURS = 0.25;

interface RawCgmLatest {
  latest_ts: string | null;
}

interface RawFetchState {
  source_id: string;
  source_kind: string;
  last_synced_at: string | null;
  updated_at: string;
}

interface RawDetectionLatest {
  latest_created_at: string | null;
}

interface RawAlertLatest {
  latest_fired_at: string | null;
  delivery: string | null;
}

export async function fetchStatus(
  timezone: string,
): Promise<StatusResponse> {
  const [cgmRows, fetchStateRows, detectionRows, alertRows] = await Promise.all([
    queryRows<RawCgmLatest>(
      `SELECT (MAX(timestamp) AT TIME ZONE $1)::text AS latest_ts FROM cgm`,
      [timezone],
    ),
    queryRows<RawFetchState>(
      `SELECT
        source_id,
        source_kind,
        (last_synced_at AT TIME ZONE $1)::text AS last_synced_at,
        (updated_at AT TIME ZONE $1)::text AS updated_at
      FROM fetch_state
      ORDER BY source_id`,
      [timezone],
    ),
    queryRows<RawDetectionLatest>(
      `SELECT (MAX(created_at) AT TIME ZONE $1)::text AS latest_created_at
       FROM detection_results`,
      [timezone],
    ),
    queryRows<RawAlertLatest>(
      `SELECT
        (fired_at AT TIME ZONE $1)::text AS latest_fired_at,
        delivery
      FROM alerts_sent
      ORDER BY fired_at DESC, id DESC
      LIMIT 1`,
      [timezone],
    ),
  ]);

  const signals: StatusSignal[] = [];

  // 1. CGM data recency
  const latestCgm = cgmRows[0]?.latest_ts ?? null;
  signals.push({
    label: "CGM data",
    timestamp: latestCgm ? latestCgm.slice(0, 16) : null,
    freshness: classifyFreshness(latestCgm, CGM_THRESHOLD_HOURS),
    detail: null,
  });

  // 2. Live meal-rise loop heartbeat (pulled out of the generic sync rows so
  // it gets the tight 15-min threshold rather than the 26h sync threshold).
  const liveCronRow =
    fetchStateRows.find((r) => r.source_id === LIVE_CRON_SOURCE) ?? null;
  signals.push({
    label: "Live loop",
    timestamp: liveCronRow?.last_synced_at
      ? liveCronRow.last_synced_at.slice(0, 16)
      : null,
    freshness: classifyFreshness(
      liveCronRow?.last_synced_at ?? null,
      LIVE_CRON_THRESHOLD_HOURS,
    ),
    detail: "5-min poll",
  });

  // 3. Nightly Tandem sync freshness — one row per (non-live-cron) source.
  const syncRows = fetchStateRows.filter(
    (r) => r.source_id !== LIVE_CRON_SOURCE,
  );
  if (syncRows.length === 0) {
    signals.push({
      label: "Tandem sync",
      timestamp: null,
      freshness: "missing",
      detail: null,
    });
  } else {
    for (const row of syncRows) {
      signals.push({
        label: `Sync: ${row.source_id}`,
        timestamp: row.last_synced_at ? row.last_synced_at.slice(0, 16) : null,
        freshness: classifyFreshness(row.last_synced_at, CGM_THRESHOLD_HOURS),
        detail: row.source_kind,
      });
    }
  }

  // 3. Live meal-rise detection recency
  const latestDetection = detectionRows[0]?.latest_created_at ?? null;
  signals.push({
    label: "Last detection",
    timestamp: latestDetection ? latestDetection.slice(0, 16) : null,
    freshness: classifyFreshness(latestDetection, DETECTION_THRESHOLD_HOURS),
    detail: null,
  });

  // 4. Latest alert (informational — no threshold drives badge, badge mirrors delivery status)
  const latestAlert = alertRows[0] ?? null;
  signals.push({
    label: "Last alert",
    timestamp: latestAlert?.latest_fired_at
      ? latestAlert.latest_fired_at.slice(0, 16)
      : null,
    // No recency threshold: use delivery as freshness stand-in
    freshness:
      latestAlert === null
        ? "missing"
        : latestAlert.delivery === "sent"
          ? "ok"
          : latestAlert.delivery === "failed"
            ? "stale"
            : "ok", // pending = ok (in-flight)
    detail: latestAlert?.delivery ?? null,
  });

  return { signals, timezone };
}
