import { queryRows } from "@/lib/queries/db";
import type { AlertsResponse } from "@/lib/types/api";
import { shapeAlertRows, type RawAlertSentRow } from "@/lib/alerts";

/**
 * Alerts history from alerts_sent (db/migrations/0001_init.sql):
 * id, alert_kind, fired_at, pump_serial, event_ref, payload, delivery.
 * No pump filter: pump_serial is nullable (not all alert kinds are
 * pump-scoped), and no join to detection_results in v1.
 */
export async function fetchAlertsHistory(
  page: number,
  pageSize: number,
  timezone: string,
): Promise<AlertsResponse> {
  const offset = (page - 1) * pageSize;

  const sql = `
    SELECT
      id::text AS id,
      alert_kind,
      (fired_at AT TIME ZONE $1)::text AS fired_at_local,
      pump_serial,
      event_ref,
      delivery,
      payload,
      COUNT(*) OVER()::text AS total
    FROM alerts_sent
    ORDER BY fired_at DESC, id DESC
    LIMIT ${pageSize} OFFSET ${offset}
  `;

  const rows = await queryRows<RawAlertSentRow>(sql, [timezone]);
  return {
    alerts: shapeAlertRows(rows),
    page,
    page_size: pageSize,
    total: rows.length > 0 ? Number(rows[0].total) : 0,
  };
}
