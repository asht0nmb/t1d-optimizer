import { loadBgTargets, getPumpSerial, getTimezone } from "@/lib/config";
import { createServiceClient } from "@/lib/supabase/server";
import { fetchCgmDateBounds } from "@/lib/queries/date-bounds";
import { jsonOk } from "@/lib/api/route";

export async function GET() {
  const timezone = getTimezone();
  let dateBounds = null;
  try {
    dateBounds = await fetchCgmDateBounds(
      createServiceClient(),
      timezone,
      getPumpSerial(),
    );
  } catch {
    // Keep config route resilient when data lookup is unavailable.
  }

  return jsonOk({
    bg_targets: loadBgTargets(),
    timezone,
    date_bounds: dateBounds,
  });
}
