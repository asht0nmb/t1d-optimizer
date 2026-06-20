import { queryRows } from "@/lib/queries/db";
import { loadBgTargets } from "@/lib/config";
import type { AgpResponse } from "@/lib/types/api";
import { shapeAgpHours, type RawAgpRow } from "@/lib/agp";
import { resolveAnchorDay, windowStart } from "@/lib/queries/window-anchor";
import { dayRangeUtc } from "@/lib/dates";

export async function fetchAgpProfile(
  days: number,
  timezone: string,
  pumpSerial?: string,
): Promise<AgpResponse> {
  const targets = loadBgTargets();
  const anchorDay = await resolveAnchorDay(timezone, pumpSerial);
  const startDay = windowStart(anchorDay, days);
  const { since, until } = dayRangeUtc(startDay, anchorDay, timezone);
  const params: unknown[] = [timezone, since.toISOString(), until.toISOString()];
  let pumpClause = "";
  if (pumpSerial) {
    params.push(pumpSerial);
    pumpClause = `AND pump_serial = $${params.length}`;
  }

  // Must match core/metrics/agp.py (5/25/50/75/95 by local hour).
  // One row per local hour 0-23; hours with no readings carry NULL
  // percentiles and n = 0.
  const sql = `
    WITH hours AS (
      SELECT generate_series(0, 23) AS hour
    ),
    agg AS (
      SELECT
        EXTRACT(HOUR FROM timestamp AT TIME ZONE $1)::int AS hour,
        PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY bg_mgdl)::float AS p05,
        PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY bg_mgdl)::float AS p25,
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY bg_mgdl)::float AS p50,
        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY bg_mgdl)::float AS p75,
        PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY bg_mgdl)::float AS p95,
        COUNT(*)::int AS n
      FROM cgm
      WHERE timestamp >= $2::timestamptz
        AND timestamp < $3::timestamptz
        ${pumpClause}
      GROUP BY 1
    )
    SELECT
      h.hour::int AS hour,
      a.p05,
      a.p25,
      a.p50,
      a.p75,
      a.p95,
      COALESCE(a.n, 0)::int AS n
    FROM hours h
    LEFT JOIN agg a ON a.hour = h.hour
    ORDER BY h.hour
  `;

  const rows = await queryRows<RawAgpRow>(sql, params);
  return {
    window_days: days,
    hours: shapeAgpHours(rows),
    bg_targets: targets,
  };
}
