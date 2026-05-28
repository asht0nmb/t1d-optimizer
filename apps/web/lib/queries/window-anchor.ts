import { addDays, format, parseISO } from "date-fns";
import { queryRows } from "@/lib/queries/db";

interface AnchorRow {
  anchor_day: string | null;
}

export async function resolveAnchorDay(
  timezone: string,
  pumpSerial?: string,
): Promise<string> {
  const params: unknown[] = [timezone];
  let pumpClause = "";
  if (pumpSerial) {
    params.push(pumpSerial);
    pumpClause = `WHERE pump_serial = $${params.length}`;
  }
  const rows = await queryRows<AnchorRow>(
    `
      SELECT MAX((timestamp AT TIME ZONE $1)::date)::text AS anchor_day
      FROM cgm
      ${pumpClause}
    `,
    params,
  );
  return rows[0]?.anchor_day ?? format(new Date(), "yyyy-MM-dd");
}

export function windowStart(anchorDay: string, days: number): string {
  return format(addDays(parseISO(anchorDay), -(days - 1)), "yyyy-MM-dd");
}
