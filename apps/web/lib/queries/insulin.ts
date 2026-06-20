import { queryRows } from "@/lib/queries/db";
import type { InsulinDayRow, InsulinResponse } from "@/lib/types/api";
import { resolveAnchorDay, windowStart } from "@/lib/queries/window-anchor";
import { dayRangeUtc } from "@/lib/dates";

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
  const { since, until } = dayRangeUtc(startDay, anchorDay, timezone);
  // $2/$3 are local-day strings for the generate_series spine; $4/$5 are the
  // UTC instant bounds used for the timestamptz row filters.
  const params: unknown[] = [
    timezone,
    startDay,
    anchorDay,
    since.toISOString(),
    until.toISOString(),
  ];
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
      WHERE timestamp >= $4::timestamptz
        AND timestamp < $5::timestamptz
        ${pumpClause}
      GROUP BY 1
    ),
    basal_windowed AS (
      -- Integrate by true inter-row duration, per local day. Mirrors
      -- detection/features.py::_integrate_basal: each row spans
      -- [ts, min(next_ts, day_end)); the final row of a day extends to that
      -- day's end. A fixed rate * 5/60 cadence assumption is wrong because
      -- Tandem basal rows are event-driven (emitted only on rate changes).
      SELECT
        (timestamp AT TIME ZONE $1)::date AS day,
        commanded_rate,
        timestamp AS row_ts,
        -- End of this row's local day, expressed as a UTC instant.
        ((((timestamp AT TIME ZONE $1)::date + interval '1 day')
          AT TIME ZONE $1)) AS day_end,
        LEAD(timestamp) OVER (
          PARTITION BY pump_serial, (timestamp AT TIME ZONE $1)::date
          ORDER BY timestamp
        ) AS next_ts
      FROM basal
      WHERE timestamp >= $4::timestamptz
        AND timestamp < $5::timestamptz
        ${pumpClause}
    ),
    basal_daily AS (
      SELECT
        day,
        SUM(
          commanded_rate
          * EXTRACT(EPOCH FROM (LEAST(COALESCE(next_ts, day_end), day_end) - row_ts))
          / 3600.0
        )::float AS basal_units
      FROM basal_windowed
      GROUP BY day
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
