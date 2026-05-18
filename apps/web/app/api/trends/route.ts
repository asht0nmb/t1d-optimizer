import { fetchTrends } from "@/lib/queries/trends";
import { getPumpSerial, getTimezone } from "@/lib/config";
import { jsonError, jsonOk, parseIntParam } from "@/lib/api/route";

export async function GET(req: Request) {
  const days = parseIntParam(
    new URL(req.url).searchParams.get("days"),
    14,
  ) as 7 | 14 | 30;
  const windowDays = ([7, 14, 30] as const).includes(days as 7 | 14 | 30)
    ? (days as 7 | 14 | 30)
    : 14;
  try {
    const data = await fetchTrends(
      windowDays,
      getTimezone(),
      getPumpSerial(),
    );
    return jsonOk(data);
  } catch (e) {
    return jsonError(
      e instanceof Error ? e.message : "Failed to load trends",
      500,
    );
  }
}
