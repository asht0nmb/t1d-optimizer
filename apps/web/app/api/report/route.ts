import { jsonError, jsonOk, parseIntParam } from "@/lib/api/route";
import { requireSession } from "@/lib/api/auth";
import type { CgmReportResponse } from "@/lib/types/api";

/**
 * Clinical CGM report proxy.
 *
 * Session-guarded (RLS is bypassed by the data layer, so this guard is the
 * only thing keeping the endpoint private). Proxies server-side to the metrics
 * worker (the repo-root Vercel Python project) which computes the report via
 * core.metrics.compute_cgm_report — the single source of truth shared with the
 * local Streamlit "Report" page. The formulas are never re-derived here.
 *
 * Env:
 *   METRICS_WORKER_URL — base URL of the worker project
 *     (e.g. https://<worker-project>.vercel.app).
 *   CRON_SECRET — reused bearer secret for the worker (same value the worker
 *     verifies).
 */
export async function GET(req: Request) {
  const denied = await requireSession();
  if (denied) return denied;

  const days = parseIntParam(
    new URL(req.url).searchParams.get("days"),
    14,
  ) as 14 | 30 | 90;
  const windowDays = ([14, 30, 90] as const).includes(days as 14 | 30 | 90)
    ? days
    : 14;

  const base = process.env.METRICS_WORKER_URL;
  const secret = process.env.CRON_SECRET;
  if (!base || !secret) {
    return jsonError("Metrics worker not configured", 500);
  }

  try {
    const res = await fetch(
      `${base.replace(/\/$/, "")}/api/metrics_report?days=${windowDays}`,
      {
        headers: { Authorization: `Bearer ${secret}` },
        cache: "no-store",
      },
    );
    if (!res.ok) {
      return jsonError(`Metrics worker error (${res.status})`, 502);
    }
    const data = (await res.json()) as CgmReportResponse;
    return jsonOk(data);
  } catch (e) {
    return jsonError(
      e instanceof Error ? e.message : "Failed to load report",
      500,
    );
  }
}
