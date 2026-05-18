import { queryRows } from "@/lib/queries/db";
import type { InsulinDayRow, InsulinResponse } from "@/lib/types/api";

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
  const params: unknown[] = [days, timezone];
  let pumpClause = "";
  if (pumpSerial) {
    params.push(pumpSerial);
    pumpClause = `AND pump_serial = $${params.length}`;
  }

  const sql = `
    WITH days AS (
      SELECT generate_series(
        CURRENT_DATE - ($1::int - 1) * interval '1 day',
        CURRENT_DATE,
        interval '1 day'
      )::date AS day
    ),
    bolus_daily AS (
      SELECT
        (timestamp AT TIME ZONE $2)::date AS day,
        SUM(insulin_units)::float AS bolus_units
      FROM bolus
      WHERE timestamp >= CURRENT_DATE - ($1::int - 1) * interval '1 day'
        ${pumpClause}
      GROUP BY 1
    ),
    basal_daily AS (
      SELECT
        (timestamp AT TIME ZONE $2)::date AS day,
        SUM(commanded_rate * 5.0 / 60.0)::float AS basal_units
      FROM basal
      WHERE timestamp >= CURRENT_DATE - ($1::int - 1) * interval '1 day'
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
