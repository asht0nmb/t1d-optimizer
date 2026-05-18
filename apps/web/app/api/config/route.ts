import { loadBgTargets, getTimezone } from "@/lib/config";
import { jsonOk } from "@/lib/api/route";

export async function GET() {
  return jsonOk({
    bg_targets: loadBgTargets(),
    timezone: getTimezone(),
  });
}
