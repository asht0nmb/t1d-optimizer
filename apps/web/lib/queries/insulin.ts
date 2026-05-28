import { queryRows } from "@/lib/queries/db";
import type { InsulinDayRow, InsulinResponse } from "@/lib/types/api";
import { resolveAnchorDay, windowStart } from "@/lib/queries/window-anchor";

interface InsulinRow {
  day: string;
  bolus_units: string;
  basal_units: string;
}

export async function fetchInsulinHistory(
  days: number,
  timezone: string,
  pumpSerial?: string,
): Promise<InsulinResponse> {
  const anchorDay = await resolveAnchorDay(timezone, pumpSerial);
  const startDay = windowStart(anchorDay, days);
  const params: unknown[] = [timezone, startDay, anchorDay];
  let pumpClause = "";
  if (pumpSerial) {
    params.push(pumpSerial);
    pumpClause = `AND pump_serial = $${params.length}`;
  }

  const sql = `
    WITH days AS (
      SELECT generate_series(
        $2::date,
        $3::date,
        interval '1 day'
      )::date AS day
    ),
    bolus_daily AS (
      SELECT
        (timestamp AT TIME ZONE $1)::date AS day,
        SUM(insulin_units)::float AS bolus_units
      FROM bolus
      WHERE timestamp >= $2::date
        AND timestamp < ($3::date + interval '1 day')
        ${pumpClause}
      GROUP BY 1
    ),
    basal_daily AS (
      SELECT
        (timestamp AT TIME ZONE $1)::date AS day,
        SUM(commanded_rate * 5.0 / 60.0)::float AS basal_units
      FROM basal
      WHERE timestamp >= $2::date
        AND timestamp < ($3::date + interval '1 day')
        ${pumpClause}
      GROUP BY 1
    )
    SELECT
      d.day::text,
      COALESCE(b.bolus_units, 0) AS bolus_units,
      COALESCE(bs.basal_units, 0) AS basal_units
    FROM days d
    LEFT JOIN bolus_daily b ON b.day = d.day
    LEFT JOIN basal_daily bs ON bs.day = d.day
    ORDER BY d.day
  `;

  const rows = await queryRows<InsulinRow>(sql, params);
  const dayRows: InsulinDayRow[] = rows.map((r) => {
    const bolus = Number(r.bolus_units);
    const basal = Number(r.basal_units);
    return {
      date: r.day.slice(0, 10),
      bolus_units: bolus,
      basal_units: basal,
      tdd_units: bolus + basal,
    };
  });

  return { days: dayRows };
}
