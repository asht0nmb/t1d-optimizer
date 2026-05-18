import { queryRows } from "@/lib/queries/db";
import { loadBgTargets } from "@/lib/config";
import type { TirTrendPoint, TrendsResponse } from "@/lib/types/api";

interface TrendRow {
  day: string;
  tir_pct: string;
  below_pct: string;
  above_pct: string;
  in_range_pct: string;
  reading_count: string;
}

export async function fetchTrends(
  windowDays: 7 | 14 | 30,
  timezone: string,
  pumpSerial?: string,
): Promise<TrendsResponse> {
  const targets = loadBgTargets();
  const params: unknown[] = [targets.low, targets.high, windowDays, timezone];
  let pumpClause = "";
  if (pumpSerial) {
    params.push(pumpSerial);
    pumpClause = `AND pump_serial = $${params.length}`;
  }

  const sql = `
    WITH daily AS (
      SELECT
        (timestamp AT TIME ZONE $4)::date AS day,
        bg_mgdl
      FROM cgm
      WHERE timestamp >= (CURRENT_DATE - ($3::int - 1) * interval '1 day')
        ${pumpClause}
    )
    SELECT
      day::text,
      100.0 * AVG(CASE WHEN bg_mgdl BETWEEN $1 AND $2 THEN 1.0 ELSE 0.0 END) AS tir_pct,
      100.0 * AVG(CASE WHEN bg_mgdl < $1 THEN 1.0 ELSE 0.0 END) AS below_pct,
      100.0 * AVG(CASE WHEN bg_mgdl > $2 THEN 1.0 ELSE 0.0 END) AS above_pct,
      100.0 * AVG(CASE WHEN bg_mgdl BETWEEN $1 AND $2 THEN 1.0 ELSE 0.0 END) AS in_range_pct,
      COUNT(*)::int AS reading_count
    FROM daily
    GROUP BY day
    ORDER BY day
  `;

  const rows = await queryRows<TrendRow>(sql, params);
  const points: TirTrendPoint[] = rows.map((r) => ({
    date: r.day.slice(0, 10),
    tir_pct: Number(r.tir_pct),
    below_pct: Number(r.below_pct),
    above_pct: Number(r.above_pct),
    in_range_pct: Number(r.in_range_pct),
    reading_count: Number(r.reading_count),
  }));

  return { window_days: windowDays, points, bg_targets: targets };
}
