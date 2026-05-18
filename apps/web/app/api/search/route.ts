import { searchDays } from "@/lib/queries/search";
import { getPumpSerial, getTimezone } from "@/lib/config";
import { jsonError, jsonOk, parseIntParam } from "@/lib/api/route";

export async function GET(req: Request) {
  const sp = new URL(req.url).searchParams;
  const tirBelow = sp.get("tir_below");
  const alarmsAbove = sp.get("alarms_above");
  const lowsAbove = sp.get("lows_above");
  try {
    const data = await searchDays(
      {
        tirBelow: tirBelow ? Number(tirBelow) : undefined,
        alarmsAbove: alarmsAbove ? Number(alarmsAbove) : undefined,
        lowsAbove: lowsAbove ? Number(lowsAbove) : undefined,
        page: parseIntParam(sp.get("page"), 1),
        pageSize: parseIntParam(sp.get("page_size"), 30),
        timezone: getTimezone(),
      },
      getPumpSerial(),
    );
    return jsonOk(data);
  } catch (e) {
    return jsonError(e instanceof Error ? e.message : "Search failed", 500);
  }
}
