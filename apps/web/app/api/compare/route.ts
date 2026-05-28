import { coerceDateParam } from "@/lib/dates";
import { fetchCompare } from "@/lib/queries/compare";
import { createServiceClient } from "@/lib/supabase/server";
import { getPumpSerial, getTimezone } from "@/lib/config";
import { jsonError, jsonOk } from "@/lib/api/route";

export async function GET(req: Request) {
  const sp = new URL(req.url).searchParams;
  const dateA = coerceDateParam(sp.get("a"));
  const dateB = coerceDateParam(sp.get("b"));
  if (!dateA || !dateB) {
    return jsonError("a and b query params required (YYYY-MM-DD)", 400);
  }
  try {
    const data = await fetchCompare(
      createServiceClient(),
      dateA,
      dateB,
      getTimezone(),
      getPumpSerial(),
    );
    return jsonOk(data);
  } catch (e) {
    return jsonError(e instanceof Error ? e.message : "Compare failed", 500);
  }
}
