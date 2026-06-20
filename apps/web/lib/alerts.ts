/** Pure alerts-history helpers (row shaping + pagination clamps) — no pg imports so vitest can load them. */
import type { AlertHistoryRow } from "@/lib/types/api";

export const DEFAULT_PAGE_SIZE = 30;
export const MAX_PAGE_SIZE = 100;

/** Raw pg row from alerts_sent (see db/migrations/0001_init.sql). */
export interface RawAlertSentRow {
  id: string | number;
  alert_kind: string;
  fired_at_local: string;
  pump_serial: string | null;
  event_ref: string | null;
  delivery: string;
  payload: unknown;
  total: string | number;
}

/** Best-effort human message from the jsonb payload (detector-owned schema). */
export function alertMessage(payload: unknown): string | null {
  if (payload && typeof payload === "object" && "message" in payload) {
    const m = (payload as { message?: unknown }).message;
    if (typeof m === "string" && m.length > 0) return m;
  }
  return null;
}

export function shapeAlertRows(rows: RawAlertSentRow[]): AlertHistoryRow[] {
  return rows.map((r) => ({
    id: Number(r.id),
    alert_kind: r.alert_kind,
    // "YYYY-MM-DD HH:MM:SS.ffffff" (local) -> "YYYY-MM-DD HH:MM"
    fired_at: String(r.fired_at_local).slice(0, 16),
    pump_serial: r.pump_serial ?? null,
    event_ref: r.event_ref ?? null,
    delivery: r.delivery,
    message: alertMessage(r.payload),
  }));
}

export function clampPage(page: number): number {
  return Number.isFinite(page) && page >= 1 ? Math.floor(page) : 1;
}

export function clampPageSize(
  size: number,
  fallback: number = DEFAULT_PAGE_SIZE,
): number {
  if (!Number.isFinite(size) || size < 1) return fallback;
  return Math.min(Math.floor(size), MAX_PAGE_SIZE);
}
