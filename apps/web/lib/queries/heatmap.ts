import { queryRows } from "@/lib/queries/db";
import type { HeatmapCell, HeatmapResponse } from "@/lib/types/api";

interface HeatmapRow {
  day: string;
  hour: number;
  avg_bg: string | null;
  median_bg: string | null;
  n: string;
}

export async function fetchHeatmap(
  dateFrom: string,
  dateTo: string,
  timezone: string,
  pumpSerial?: string,
): Promise<HeatmapResponse> {
  const params: unknown[] = [dateFrom, dateTo, timezone];
  let pumpClause = "";
  if (pumpSerial) {
    params.push(pumpSerial);
    pumpClause = `AND pump_serial = $${params.length}`;
  }

  const sql = `
    SELECT
      (timestamp AT TIME ZONE $3)::date AS day,
      EXTRACT(HOUR FROM timestamp AT TIME ZONE $3)::int AS hour,
      AVG(bg_mgdl)::float AS avg_bg,
      PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY bg_mgdl)::float AS median_bg,
      COUNT(*)::int AS n
    FROM cgm
    WHERE timestamp >= $1::date
      AND timestamp < ($2::date + interval '1 day')
      ${pumpClause}
    GROUP BY 1, 2
    ORDER BY 1, 2
  `;

  const rows = await queryRows<HeatmapRow>(sql, params);
  const cells: HeatmapCell[] = rows.map((r) => ({
    date:
      typeof r.day === "string"
        ? r.day.slice(0, 10)
        : new Date(r.day).toISOString().slice(0, 10),
    hour: Number(r.hour),
    avg_bg: r.avg_bg != null ? Number(r.avg_bg) : null,
    median_bg: r.median_bg != null ? Number(r.median_bg) : null,
    n: Number(r.n),
  }));

  return { cells, date_from: dateFrom, date_to: dateTo };
}
