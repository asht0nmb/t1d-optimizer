import { jsonError, jsonOk } from "@/lib/api/route";
import { verifyCronAuth } from "@/lib/cron/auth";

/**
 * Manual / health-check route for the meal-rise cron.
 * Production schedule uses the Python serverless handler at /api/meal_rise_cron.
 */
export async function GET(request: Request) {
  if (!verifyCronAuth(request)) {
    return jsonError("unauthorized", 401);
  }
  return jsonOk({
    ok: true,
    handler: "/api/meal_rise_cron",
    message: "Vercel Cron invokes the Python serverless function at /api/meal_rise_cron",
  });
}
