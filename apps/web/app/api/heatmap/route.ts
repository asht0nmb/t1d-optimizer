import { fetchHeatmap } from "@/lib/queries/heatmap";
import { getPumpSerial, getTimezone } from "@/lib/config";
import { coerceDateParam } from "@/lib/dates";
import { jsonError, jsonOk } from "@/lib/api/route";

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const from = coerceDateParam(searchParams.get("from"));
  const to = coerceDateParam(searchParams.get("to"));
  if (!from || !to) {
    return jsonError("from and to query params required (YYYY-MM-DD)", 400);
  }
  if (from > to) {
    return jsonError("from must be <= to", 400);
  }
  try {
    const data = await fetchHeatmap(
      from,
      to,
      getTimezone(),
      getPumpSerial(),
    );
    return jsonOk(data);
  } catch (e) {
    return jsonError(
      e instanceof Error ? e.message : "Failed to load heatmap",
      500,
    );
  }
}
