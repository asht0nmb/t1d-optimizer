import { coerceDateParam } from "@/lib/dates";
import { getPumpSerial, getTimezone } from "@/lib/config";
import { fetchDayView } from "@/lib/queries/day";
import { createServiceClient } from "@/lib/supabase/server";
import { jsonError, jsonOk } from "@/lib/api/route";

export async function GET(
  _req: Request,
  { params }: { params: { date: string } },
) {
  const date = coerceDateParam(params.date);
  if (!date) {
    return jsonError("Invalid date; use YYYY-MM-DD", 400);
  }
  try {
    const data = await fetchDayView(
      createServiceClient(),
      date,
      getTimezone(),
      getPumpSerial(),
    );
    return jsonOk(data);
  } catch (e) {
    return jsonError(e instanceof Error ? e.message : "Failed to load day", 500);
  }
}
