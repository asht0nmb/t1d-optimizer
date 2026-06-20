import { loadBgTargets, getPumpSerial, getTimezone } from "@/lib/config";
import { createServiceClient } from "@/lib/supabase/server";
import { fetchCgmDateBounds } from "@/lib/queries/date-bounds";
import { jsonOk } from "@/lib/api/route";
import { requireSession } from "@/lib/api/auth";

export async function GET() {
  const denied = await requireSession();
  if (denied) return denied;
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
