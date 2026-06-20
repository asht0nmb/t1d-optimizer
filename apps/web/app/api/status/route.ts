import { fetchStatus } from "@/lib/queries/status";
import { getTimezone } from "@/lib/config";
import { jsonError, jsonOk } from "@/lib/api/route";
import { requireSession } from "@/lib/api/auth";

export async function GET() {
  const denied = await requireSession();
  if (denied) return denied;
  try {
    const data = await fetchStatus(getTimezone());
    return jsonOk(data);
  } catch (e) {
    return jsonError(
      e instanceof Error ? e.message : "Failed to load status",
      500,
    );
  }
}
