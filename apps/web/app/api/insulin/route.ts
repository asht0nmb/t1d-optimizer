import { fetchInsulinHistory } from "@/lib/queries/insulin";
import { getPumpSerial, getTimezone } from "@/lib/config";
import { jsonError, jsonOk, parseIntParam } from "@/lib/api/route";
import { requireSession } from "@/lib/api/auth";

export async function GET(req: Request) {
  const denied = await requireSession();
  if (denied) return denied;
  const days = parseIntParam(new URL(req.url).searchParams.get("days"), 30);
  try {
    const data = await fetchInsulinHistory(
      days,
      getTimezone(),
      getPumpSerial(),
    );
    return jsonOk(data);
  } catch (e) {
    return jsonError(
      e instanceof Error ? e.message : "Failed to load insulin history",
      500,
    );
  }
}
