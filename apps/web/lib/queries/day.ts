import type { SupabaseClient } from "@supabase/supabase-js";
import { dayWindowUtc } from "@/lib/dates";
import { loadBgTargets } from "@/lib/config";
import { computeTirBreakdown } from "@/lib/tir";
import type {
  AlarmRow,
  BasalRow,
  BolusRow,
  CgmGapRow,
  CgmPoint,
  DaySummary,
  DayViewResponse,
  RequestRow,
  SiteIssueRow,
  SuspensionRow,
} from "@/lib/types/api";

function pumpFilter(
  client: SupabaseClient,
  table: string,
  pumpSerial?: string,
) {
  let q = client.from(table).select("*");
  if (pumpSerial) q = q.eq("pump_serial", pumpSerial);
  return q;
}

export async function fetchDayView(
  client: SupabaseClient,
  dateStr: string,
  timezone: string,
  pumpSerial?: string,
): Promise<DayViewResponse> {
  const { since, until } = dayWindowUtc(dateStr, timezone);
  const sinceIso = since.toISOString();
  const untilIso = until.toISOString();
  const targets = loadBgTargets();

  const [
    cgmRes,
    bolusRes,
    requestsRes,
    basalRes,
    suspensionRes,
    alarmsRes,
    siteRes,
    gapsRes,
  ] = await Promise.all([
    pumpFilter(client, "cgm", pumpSerial)
      .gte("timestamp", sinceIso)
      .lt("timestamp", untilIso)
      .order("timestamp"),
    pumpFilter(client, "bolus", pumpSerial)
      .gte("timestamp", sinceIso)
      .lt("timestamp", untilIso)
      .order("timestamp"),
    pumpFilter(client, "requests", pumpSerial)
      .gte("timestamp", sinceIso)
      .lt("timestamp", untilIso)
      .order("timestamp"),
    pumpFilter(client, "basal", pumpSerial)
      .gte("timestamp", sinceIso)
      .lt("timestamp", untilIso)
      .order("timestamp"),
    pumpFilter(client, "suspension", pumpSerial)
      .gte("suspend_timestamp", sinceIso)
      .lt("suspend_timestamp", untilIso)
      .order("suspend_timestamp"),
    pumpFilter(client, "alarms", pumpSerial)
      .gte("timestamp", sinceIso)
      .lt("timestamp", untilIso)
      .order("timestamp"),
    pumpFilter(client, "site_issues", pumpSerial)
      .lt("first_occlusion_ts", untilIso)
      .or(`last_occlusion_ts.gte.${sinceIso},last_occlusion_ts.is.null`),
    pumpFilter(client, "cgm_gaps", pumpSerial)
      .lt("start_ts", untilIso)
      .or(`end_ts.gte.${sinceIso},end_ts.is.null`),
  ]);

  const err =
    cgmRes.error ??
    bolusRes.error ??
    requestsRes.error ??
    basalRes.error ??
    suspensionRes.error ??
    alarmsRes.error ??
    siteRes.error ??
    gapsRes.error;
  if (err) throw err;

  const cgm: CgmPoint[] = (cgmRes.data ?? []).map((r) => ({
    timestamp: r.timestamp as string,
    bg_mgdl: Number(r.bg_mgdl),
    backfilled: Boolean(r.backfilled),
  }));

  const bolus: BolusRow[] = (bolusRes.data ?? []).map((r) => ({
    timestamp: r.timestamp as string,
    bolus_id: Number(r.bolus_id),
    insulin_units: Number(r.insulin_units),
  }));

  const requests: RequestRow[] = (requestsRes.data ?? []).map((r) => ({
    timestamp: r.timestamp as string,
    bolus_id: Number(r.bolus_id),
    carbs_g: Number(r.carbs_g),
    bg_mgdl: Number(r.bg_mgdl),
    bolus_source: String(r.bolus_source),
    bolus_category: (r.bolus_category as string) ?? null,
    total_requested: Number(r.total_requested),
  }));

  const basal: BasalRow[] = (basalRes.data ?? []).map((r) => ({
    timestamp: r.timestamp as string,
    commanded_rate: Number(r.commanded_rate),
    rate_source: String(r.rate_source),
  }));

  const suspension: SuspensionRow[] = (suspensionRes.data ?? []).map((r) => ({
    suspend_timestamp: r.suspend_timestamp as string,
    resume_timestamp: (r.resume_timestamp as string) ?? null,
    reason: String(r.reason),
  }));

  const alarms: AlarmRow[] = (alarmsRes.data ?? []).map((r) => ({
    timestamp: r.timestamp as string,
    alarm_name: String(r.alarm_name),
    action: String(r.action),
    category: String(r.category),
  }));

  const site_issues: SiteIssueRow[] = (siteRes.data ?? []).map((r) => ({
    first_occlusion_ts: r.first_occlusion_ts as string,
    last_occlusion_ts: (r.last_occlusion_ts as string) ?? null,
    occlusion_count: Number(r.occlusion_count),
  }));

  const cgm_gaps: CgmGapRow[] = (gapsRes.data ?? []).map((r) => ({
    start_ts: r.start_ts as string,
    end_ts: (r.end_ts as string) ?? null,
  }));

  const tir = computeTirBreakdown(
    cgm.map((p) => p.bg_mgdl),
    targets,
  );
  const totalBolus = bolus.reduce((s, b) => s + b.insulin_units, 0);
  const totalBasal = basal.reduce(
    (s, b) => s + (b.commanded_rate * 5) / 60,
    0,
  );
  const bgs = cgm.map((p) => p.bg_mgdl);

  const summary: DaySummary = {
    date: dateStr,
    tir_pct: tir.tir_pct,
    mean_bg: bgs.length ? bgs.reduce((a, b) => a + b, 0) / bgs.length : null,
    min_bg: bgs.length ? Math.min(...bgs) : null,
    max_bg: bgs.length ? Math.max(...bgs) : null,
    cgm_count: cgm.length,
    bolus_count: bolus.length,
    total_bolus_units: totalBolus,
    total_basal_units: totalBasal,
    tdd_units: totalBolus + totalBasal,
    alarm_count: alarms.length,
  };

  return {
    date: dateStr,
    timezone,
    pump_serial: pumpSerial ?? "all",
    bg_targets: targets,
    summary,
    cgm,
    bolus,
    requests,
    basal,
    suspension,
    alarms,
    site_issues,
    cgm_gaps,
  };
}
