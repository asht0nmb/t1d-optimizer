import { jsonError, jsonOk } from "@/lib/api/route";
import { verifyCronAuth } from "@/lib/cron/auth";

/**
 * Manual / health-check route for meal-rise cron operations.
 * Production schedule is executed outside Vercel web runtime.
 */
export async function GET(request: Request) {
  if (!verifyCronAuth(request)) {
    return jsonError("unauthorized", 401);
  }
  return jsonOk({
    ok: true,
    mode: "health_only",
    scheduler: "external",
    message: "Meal-rise cron execution runs outside the Next.js web deployment.",
  });
}
