import { fetchAlertsHistory } from "@/lib/queries/alerts";
import { clampPage, clampPageSize, DEFAULT_PAGE_SIZE } from "@/lib/alerts";
import { getTimezone } from "@/lib/config";
import { jsonError, jsonOk, parseIntParam } from "@/lib/api/route";
import { requireSession } from "@/lib/api/auth";

export async function GET(req: Request) {
  const denied = await requireSession();
  if (denied) return denied;
  const sp = new URL(req.url).searchParams;
  const page = clampPage(parseIntParam(sp.get("page"), 1));
  const pageSize = clampPageSize(
    parseIntParam(sp.get("page_size"), DEFAULT_PAGE_SIZE),
  );
  try {
    const data = await fetchAlertsHistory(page, pageSize, getTimezone());
    return jsonOk(data);
  } catch (e) {
    return jsonError(
      e instanceof Error ? e.message : "Failed to load alerts",
      500,
    );
  }
}
