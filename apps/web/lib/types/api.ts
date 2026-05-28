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

export interface CompareResponse {
  date_a: string;
  date_b: string;
  series_a: CgmPoint[];
  series_b: CgmPoint[];
  bg_targets: BgTargets;
}
