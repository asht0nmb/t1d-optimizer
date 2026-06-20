/** Typed API contracts — UI imports these; shape changes stay localized. */

export interface BgTargets {
  low: number;
  high: number;
  target: number;
}

export interface DateBounds {
  min_date: string;
  max_date: string;
}

export interface ConfigResponse {
  bg_targets: BgTargets;
  timezone: string;
  date_bounds: DateBounds | null;
}

export interface CgmPoint {
  timestamp: string;
  bg_mgdl: number;
  backfilled: boolean;
}

export interface BolusRow {
  timestamp: string;
  bolus_id: number;
  insulin_units: number;
}

export interface RequestRow {
  timestamp: string;
  bolus_id: number;
  carbs_g: number;
  bg_mgdl: number;
  bolus_source: string;
  bolus_category: string | null;
  total_requested: number;
}

export interface BasalRow {
  timestamp: string;
  commanded_rate: number;
  rate_source: string;
}

export interface SuspensionRow {
  suspend_timestamp: string;
  resume_timestamp: string | null;
  reason: string;
}

export interface AlarmRow {
  timestamp: string;
  alarm_name: string;
  action: string;
  category: string;
}

export interface SiteIssueRow {
  first_occlusion_ts: string;
  last_occlusion_ts: string | null;
  occlusion_count: number;
}

export interface CgmGapRow {
  start_ts: string;
  end_ts: string | null;
}

export interface DaySummary {
  date: string;
  tir_pct: number;
  mean_bg: number | null;
  min_bg: number | null;
  max_bg: number | null;
  cgm_count: number;
  bolus_count: number;
  total_bolus_units: number;
  total_basal_units: number;
  tdd_units: number;
  alarm_count: number;
}

export interface DayViewResponse {
  date: string;
  timezone: string;
  pump_serial: string;
  bg_targets: BgTargets;
  summary: DaySummary;
  cgm: CgmPoint[];
  bolus: BolusRow[];
  requests: RequestRow[];
  basal: BasalRow[];
  suspension: SuspensionRow[];
  alarms: AlarmRow[];
  site_issues: SiteIssueRow[];
  cgm_gaps: CgmGapRow[];
}

export interface HeatmapCell {
  date: string;
  hour: number;
  avg_bg: number | null;
  median_bg: number | null;
  n: number;
}

export interface HeatmapResponse {
  cells: HeatmapCell[];
  date_from: string;
  date_to: string;
}

export interface TirTrendPoint {
  date: string;
  tir_pct: number;
  below_pct: number;
  above_pct: number;
  in_range_pct: number;
  reading_count: number;
}

export interface TrendsResponse {
  window_days: 7 | 14 | 30;
  points: TirTrendPoint[];
  bg_targets: BgTargets;
}

/** One local hour of the AGP profile; NULL percentiles when the hour has no readings. */
export interface AgpHourPoint {
  hour: number;
  p05: number | null;
  p25: number | null;
  p50: number | null;
  p75: number | null;
  p95: number | null;
  n: number;
}

export interface AgpResponse {
  window_days: number;
  hours: AgpHourPoint[];
  bg_targets: BgTargets;
}

export interface InsulinDayRow {
  date: string;
  bolus_units: number;
  basal_units: number;
  tdd_units: number;
}

export interface InsulinResponse {
  days: InsulinDayRow[];
}

export interface SearchResultRow {
  date: string;
  tir_pct: number;
  alarm_count: number;
  low_count: number;
}

export interface SearchResponse {
  results: SearchResultRow[];
  page: number;
  page_size: number;
  total: number;
}

/** One alerts_sent row shaped for the UI; delivery is sent | pending | failed. */
export interface AlertHistoryRow {
  id: number;
  alert_kind: string;
  fired_at: string;
  pump_serial: string | null;
  event_ref: string | null;
  delivery: string;
  message: string | null;
}

export interface AlertsResponse {
  alerts: AlertHistoryRow[];
  page: number;
  page_size: number;
  total: number;
}

/**
 * Clinical CGM report — mirrors the Python `core.metrics.report.CgmReport`
 * dataclass field-for-field. Computed by the metrics worker
 * (`/api/metrics_report`) and proxied through `/api/report`. `null` means a
 * metric is undefined (e.g. withheld for insufficiency); `0.0` is a legitimate
 * zero. Band percentages (tbr2..tar2) are always defined.
 */
export interface CgmReportResponse {
  // Provenance
  end_date: string;
  days: number;
  tz: string;
  n_readings: number;
  expected_readings: number;
  active_pct: number;
  days_covered: number;
  meets_sufficiency: boolean;

  // Band panel (percentages)
  tbr2: number;
  tbr1: number;
  tir: number;
  tar1: number;
  tar2: number;
  tbr_total: number;
  tar_total: number;
  titr: number;
  tir_config: number;

  // Central tendency
  mean_bg: number | null;
  median_bg: number | null;
  sd_bg: number | null;
  cv_pct: number | null;
  cv_stable: boolean | null;

  // Estimated glycation
  gmi: number | null;
  ea1c: number | null;

  // Risk indices
  lbgi: number | null;
  hbgi: number | null;
  gri: number | null;
  gri_hypo: number | null;
  gri_hyper: number | null;

  // Advanced variability
  j_index: number | null;
  modd: number | null;
  conga: number | null;
  mage: number | null;
}

export interface CompareResponse {
  date_a: string;
  date_b: string;
  series_a: CgmPoint[];
  series_b: CgmPoint[];
  bg_targets: BgTargets;
}

// ---- Status page -------------------------------------------------------

/** One row from the fetch_state table. */
export interface FetchStateRow {
  source_id: string;
  source_kind: string;
  last_synced_at: string | null;
  updated_at: string;
}

/**
 * One signal row on the status page.
 * freshness: "ok" | "stale" | "missing" — drives badge colour.
 */
export interface StatusSignal {
  label: string;
  timestamp: string | null; // ISO, already converted to local time
  freshness: "ok" | "stale" | "missing";
  detail: string | null; // e.g. source_kind, delivery value, or null
}

export interface StatusResponse {
  signals: StatusSignal[];
  /** IANA timezone used for local-time conversion. */
  timezone: string;
}
