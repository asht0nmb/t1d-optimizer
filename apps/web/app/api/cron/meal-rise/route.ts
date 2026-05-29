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
    scheduler: "cron-job.org",
    executionEndpoint: "/api/meal_rise_cron",
    executionProject: "apps/cron_worker (separate Vercel project)",
    message:
      "Point cron-job.org at the cron_worker Vercel URL. See apps/cron_worker/README.md.",
  });
}
