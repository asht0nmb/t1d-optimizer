import { queryRows } from "@/lib/queries/db";
import { loadBgTargets } from "@/lib/config";
import type { SearchResponse, SearchResultRow } from "@/lib/types/api";
import { resolveAnchorDay, windowStart } from "@/lib/queries/window-anchor";
import { dayRangeUtc } from "@/lib/dates";

interface SearchRow {
  day: string;
  tir_pct: string;
  alarm_count: string;
  low_count: string;
  total: string;
}

export interface SearchFilters {
  tirBelow?: number;
  alarmsAbove?: number;
  lowsAbove?: number;
  page?: number;
  pageSize?: number;
  timezone?: string;
}

export async function searchDays(
  filters: SearchFilters,
  pumpSerial?: string,
): Promise<SearchResponse> {
  const targets = loadBgTargets();
  const tz = filters.timezone ?? "America/Los_Angeles";
  const anchorDay = await resolveAnchorDay(tz, pumpSerial);
  const startDay = windowStart(anchorDay, 365);
  const page = filters.page ?? 1;
  const pageSize = filters.pageSize ?? 30;
  const offset = (page - 1) * pageSize;

  const { since, until } = dayRangeUtc(startDay, anchorDay, tz);
  const params: unknown[] = [
    targets.low,
    targets.high,
    tz,
    since.toISOString(),
    until.toISOString(),
  ];
  const where: string[] = [
    "c.timestamp >= $4::timestamptz",
    "c.timestamp < $5::timestamptz",
  ];
  const having: string[] = [];

  if (pumpSerial) {
    params.push(pumpSerial);
    where.push(`c.pump_serial = $${params.length}`);
  }
  if (filters.tirBelow != null) {
    params.push(filters.tirBelow);
    having.push(`tir_pct < $${params.length}`);
  }
  if (filters.lowsAbove != null) {
    params.push(filters.lowsAbove);
    having.push(`low_count > $${params.length}`);
  }
  if (filters.alarmsAbove != null) {
    params.push(filters.alarmsAbove);
    having.push(`alarm_count > $${params.length}`);
  }

  const havingClause =
    having.length > 0 ? `WHERE ${having.join(" AND ")}` : "";

  const sql = `
    WITH daily AS (
      SELECT
        (c.timestamp AT TIME ZONE $3)::date AS day,
        100.0 * AVG(CASE WHEN c.bg_mgdl BETWEEN $1 AND $2 THEN 1.0 ELSE 0.0 END) AS tir_pct,
        SUM(CASE WHEN c.bg_mgdl < $1 THEN 1 ELSE 0 END)::int AS low_count
      FROM cgm c
      WHERE ${where.join(" AND ")}
      GROUP BY 1
    ),
    alarm_daily AS (
      SELECT
        (a.timestamp AT TIME ZONE $3)::date AS day,
        COUNT(*) FILTER (WHERE a.action = 'activated')::int AS alarm_count
      FROM alarms a
      WHERE a.timestamp >= $4::timestamptz
        AND a.timestamp < $5::timestamptz
        ${pumpSerial ? `AND a.pump_serial = $6` : ""}
      GROUP BY 1
    ),
    joined AS (
      SELECT
        d.day,
        d.tir_pct,
        d.low_count,
        COALESCE(ad.alarm_count, 0) AS alarm_count
      FROM daily d
      LEFT JOIN alarm_daily ad ON ad.day = d.day
    ),
    filtered AS (
      SELECT * FROM joined
      ${havingClause}
    )
    SELECT
      day::text,
      tir_pct,
      alarm_count,
      low_count,
      COUNT(*) OVER()::text AS total
    FROM filtered
    ORDER BY day DESC
    LIMIT ${pageSize} OFFSET ${offset}
  `;

  const rows = await queryRows<SearchRow>(sql, params);
  const results: SearchResultRow[] = rows.map((r) => ({
    date: r.day.slice(0, 10),
    tir_pct: Number(r.tir_pct),
    alarm_count: Number(r.alarm_count),
    low_count: Number(r.low_count),
  }));

  return {
    results,
    page,
    page_size: pageSize,
    total: rows.length > 0 ? Number(rows[0].total) : 0,
  };
}
