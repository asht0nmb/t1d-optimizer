import { formatInTimeZone } from "date-fns-tz";
import type { SupabaseClient } from "@supabase/supabase-js";
import type { DateBounds } from "@/lib/types/api";

export async function fetchCgmDateBounds(
  client: SupabaseClient,
  timezone: string,
  pumpSerial?: string,
): Promise<DateBounds | null> {
  let minQ = client.from("cgm").select("timestamp").order("timestamp", {
    ascending: true,
  });
  let maxQ = client.from("cgm").select("timestamp").order("timestamp", {
    ascending: false,
  });
  if (pumpSerial) {
    minQ = minQ.eq("pump_serial", pumpSerial);
    maxQ = maxQ.eq("pump_serial", pumpSerial);
  }

  const [minRes, maxRes] = await Promise.all([minQ.limit(1), maxQ.limit(1)]);
  const err = minRes.error ?? maxRes.error;
  if (err) throw err;

  const minTs = minRes.data?.[0]?.timestamp as string | undefined;
  const maxTs = maxRes.data?.[0]?.timestamp as string | undefined;
  if (!minTs || !maxTs) return null;

  return {
    min_date: formatInTimeZone(new Date(minTs), timezone, "yyyy-MM-dd"),
    max_date: formatInTimeZone(new Date(maxTs), timezone, "yyyy-MM-dd"),
  };
}
