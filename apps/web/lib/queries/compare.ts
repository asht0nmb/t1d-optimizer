import type { SupabaseClient } from "@supabase/supabase-js";
import { dayWindowUtc } from "@/lib/dates";
import { loadBgTargets } from "@/lib/config";
import type { CgmPoint, CompareResponse } from "@/lib/types/api";

async function fetchCgmDay(
  client: SupabaseClient,
  dateStr: string,
  timezone: string,
  pumpSerial?: string,
): Promise<CgmPoint[]> {
  const { since, until } = dayWindowUtc(dateStr, timezone);
  let q = client
    .from("cgm")
    .select("timestamp, bg_mgdl, backfilled")
    .gte("timestamp", since.toISOString())
    .lt("timestamp", until.toISOString())
    .order("timestamp");
  if (pumpSerial) q = q.eq("pump_serial", pumpSerial);
  const { data, error } = await q;
  if (error) throw error;
  return (data ?? []).map((r) => ({
    timestamp: r.timestamp as string,
    bg_mgdl: Number(r.bg_mgdl),
    backfilled: Boolean(r.backfilled),
  }));
}

export async function fetchCompare(
  client: SupabaseClient,
  dateA: string,
  dateB: string,
  timezone: string,
  pumpSerial?: string,
): Promise<CompareResponse> {
  const [series_a, series_b] = await Promise.all([
    fetchCgmDay(client, dateA, timezone, pumpSerial),
    fetchCgmDay(client, dateB, timezone, pumpSerial),
  ]);
  return {
    date_a: dateA,
    date_b: dateB,
    series_a,
    series_b,
    bg_targets: loadBgTargets(),
  };
}
