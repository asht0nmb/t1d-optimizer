"""Replay the live meal-rise detector over historical pump CGM, as if live.

Advisory and READ-ONLY. Answers: "had the live loop seen a perfectly fresh
Dexcom feed, how many alerts would it have sent, which gate rejected the rest,
and how many of those alerts would have been about a genuinely unbolused meal?"

Method (mirrors ``apps/personal/cron/detect_meal_rise.py::run_cron``):

* Step a virtual clock every ``--step-min`` minutes. At each tick, "fetch" the
  newest ``dexcom_max_count(...)`` readings that would have been visible to
  Dexcom Share (reading time + ``--latency-min`` <= tick), bucket-dedupe them to
  one per 5-min sensor bucket, take the newest as the anchor.
* Apply the live gates in the live order: freshness (``max_reading_age_minutes``),
  then the REAL ``make_window`` + REAL ``detect_meal_rise`` (min_samples,
  min_coverage, start-level bounds, slope vs base * time-of-day multiplier), then
  the refractory window (``refractory_minutes`` since the last *sent* alert).
* A mirror classifier attributes each rejection to the first failing gate. It
  never decides outcomes: the real detector does, and the replay counts any
  disagreement between the two (``mirror_mismatches`` must be 0).
* Uses the CURRENT ``config/user_config.yaml``. Never edits it.

Caveats: pump CGM and Dexcom Share come from the same G7 sensor but differ in
latency/timestamping, and the Share feed also depends on the phone app being
connected. This replay therefore measures what the detector WOULD do with a
healthy feed; it says nothing about how often the real feed was healthy.
"Freshness" rejections here are pump data gaps, not Share staleness.

Read-only DB: ``conn.set_session(readonly=True)`` + SELECT only. The DB URL is
read from ``$SUPABASE_DB_URL`` or ``--env-file`` (parsed, never exported or
copied). Per-alert detail goes to ``--out-dir`` (default ``data/reports``,
gitignored: personal health data); stdout/markdown summary is aggregate only.

Rerun::

    uv run python scripts/replay_meal_rise_live.py \\
        --start 2026-01-01 --end 2026-08-16 --live-since 2026-07-01 \\
        --env-file /path/to/.env --phases 0,1,2,3,4
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import numpy as np
import pandas as pd

from core.detection import Anchor, make_window
from core.detection.meal_rise import MealRiseConfig, MealRiseDetection, detect_meal_rise
from detection.calibration.meal_rise_scoring import (
    LABEL_LATE,
    LABEL_PRE,
    LABEL_UNCOVERED,
    ScoredInstance,
    score_instances,
)
from detection.config import AppConfig, get_config

# Gate names, in live evaluation order. ``fired`` == passed every detector gate.
G_NO_DATA = "no_data"
G_STALE = "freshness_stale_or_gap"
G_SAMPLES = "min_samples"
G_COVERAGE = "min_coverage"
G_HAS_GAP = "has_gap"  # inert live (no gaps_df); kept so the order is explicit
G_START_LOW = "start_level_below_min"
G_START_HIGH = "start_level_above_max"
G_SLOPE_MEAL = "slope_below_threshold_meal_window"
G_SLOPE_OFF = "slope_below_threshold_off_hours"
G_FIRED = "fired_candidate"
G_REFRACTORY = "refractory_suppressed"
G_ALERT = "alert_sent"
GATE_ORDER = [
    G_NO_DATA, G_STALE, G_SAMPLES, G_COVERAGE, G_HAS_GAP, G_START_LOW,
    G_START_HIGH, G_SLOPE_MEAL, G_SLOPE_OFF, G_FIRED, G_REFRACTORY, G_ALERT,
]


# --------------------------------------------------------------------------- #
# Pure replay core (no I/O)
# --------------------------------------------------------------------------- #

def max_count(cfg: MealRiseConfig) -> int:
    """Same arithmetic as ``dexcom_max_count`` in the cron module (kept local so
    this script does not import the cron module, which calls ``load_dotenv``)."""
    return int((cfg.window_minutes + cfg.fetch_buffer_minutes)
               // cfg.expected_interval_minutes) + cfg.fetch_readings_padding


def theil_sen(times_min: list[float], values: list[float]) -> float:
    """Median of pairwise slopes; identical to the detector's estimator."""
    slopes = [
        (values[j] - values[i]) / (times_min[j] - times_min[i])
        for i in range(len(values)) for j in range(i + 1, len(values))
        if times_min[j] - times_min[i] > 0
    ]
    return float(np.median(slopes)) if slopes else float("nan")


def time_multiplier(cfg: MealRiseConfig, hour: int) -> float:
    for mw in cfg.meal_windows:
        if int(mw.get("start_hour", 0)) <= hour <= int(mw.get("end_hour", 0)):
            return float(mw.get("multiplier", 1.0))
    return cfg.off_hours_multiplier


def classify_window(window, cfg: MealRiseConfig) -> tuple[str, dict]:
    """First failing detector gate for ``window`` (mirror of detect_meal_rise)."""
    info: dict = {}
    if window.n_samples < cfg.min_samples:
        return G_SAMPLES, info
    if window.coverage < cfg.min_coverage:
        return G_COVERAGE, info
    if window.has_gap:
        return G_HAS_GAP, info
    ts = window.samples["timestamp"]
    times = [(t - ts.iloc[0]).total_seconds() / 60.0 for t in ts]
    values = window.samples["bg_mgdl"].astype(float).tolist()
    slope = theil_sen(times, values)
    start = int(values[0])
    hour = window.anchor.timestamp.hour
    mult = time_multiplier(cfg, hour)
    thr = cfg.base_slope_mgdl_per_min * mult
    info = {"slope": slope, "threshold": thr, "ratio": slope / thr if thr else float("nan"),
            "mult": mult, "start": start}
    if start < cfg.start_level_min:
        return G_START_LOW, info
    if start > cfg.start_level_max:
        return G_START_HIGH, info
    if slope >= thr:
        return G_FIRED, info
    in_meal_window = any(
        int(m["start_hour"]) <= hour <= int(m["end_hour"]) for m in cfg.meal_windows)
    return (G_SLOPE_MEAL if in_meal_window else G_SLOPE_OFF), info


@dataclass
class ReplayResult:
    gate_counts: Counter = field(default_factory=Counter)
    ticks: int = 0
    alerts: list[MealRiseDetection] = field(default_factory=list)
    # Per fired-candidate tick: (anchor, outcome) for event clustering/analysis.
    fired_ticks: list[tuple[datetime, str]] = field(default_factory=list)
    slope_ratio_hist: Counter = field(default_factory=Counter)  # slope-rejected only
    mirror_mismatches: int = 0
    fresh_ticks_by_month: Counter = field(default_factory=Counter)
    ticks_by_month: Counter = field(default_factory=Counter)


def replay(
    cgm: pd.DataFrame,
    config: AppConfig,
    *,
    start: datetime,
    end: datetime,
    step_min: int = 5,
    latency_min: float = 2.0,
    phase_min: float = 0.0,
) -> ReplayResult:
    """Replay the live loop over ``cgm`` (tz-aware ``timestamp`` + ``bg_mgdl``)."""
    cfg = config.meal_rise
    tz = ZoneInfo(config.timezone)
    df = cgm.sort_values("timestamp").reset_index(drop=True)
    local = df.copy()
    local["timestamp"] = local["timestamp"].dt.tz_convert(tz)
    local["_bucket"] = df["timestamp"].dt.tz_convert("UTC").dt.floor(
        f"{cfg.expected_interval_minutes}min")
    # Epoch seconds (unit-agnostic: pandas 3 may store us, older versions ns).
    ts_s = ((df["timestamp"].dt.tz_convert("UTC") - pd.Timestamp(0, tz="UTC"))
            // pd.Timedelta(seconds=1)).to_numpy(dtype="int64")
    n_fetch = max_count(cfg)
    lat_s = latency_min * 60.0
    out = ReplayResult()
    last_alert: datetime | None = None
    refractory = timedelta(minutes=cfg.refractory_minutes)
    max_age = cfg.max_reading_age_minutes
    pre = timedelta(minutes=cfg.window_minutes)

    t = start + timedelta(minutes=phase_min)
    step = timedelta(minutes=step_min)
    while t < end:
        out.ticks += 1
        month = t.astimezone(tz).strftime("%Y-%m")
        out.ticks_by_month[month] += 1
        visible = int(np.searchsorted(ts_s, t.timestamp() - lat_s, side="right"))
        if visible == 0:
            out.gate_counts[G_NO_DATA] += 1
            t += step
            continue
        tail = local.iloc[max(0, visible - n_fetch):visible]
        tail = (tail.sort_values("timestamp").groupby("_bucket", as_index=False).last()
                .sort_values("timestamp").reset_index(drop=True))
        latest_ts = tail["timestamp"].iloc[-1].to_pydatetime()
        age_min = (t - latest_ts).total_seconds() / 60.0
        if age_min > max_age:
            out.gate_counts[G_STALE] += 1
            t += step
            continue
        out.fresh_ticks_by_month[month] += 1
        window = make_window(tail[["timestamp", "bg_mgdl"]],
                             Anchor(timestamp=latest_ts, kind="live"),
                             pre=pre, post=timedelta(0))
        det = detect_meal_rise(window, cfg)
        gate, info = classify_window(window, cfg)
        if (det is not None) != (gate == G_FIRED):
            out.mirror_mismatches += 1
        if det is None:
            out.gate_counts[gate] += 1
            if gate in (G_SLOPE_MEAL, G_SLOPE_OFF) and info.get("ratio") == info.get("ratio"):
                out.slope_ratio_hist[min(int(max(info["ratio"], 0) * 10) / 10, 1.0)] += 1
            t += step
            continue
        out.gate_counts[G_FIRED] += 1
        if last_alert is not None and (t - last_alert) < refractory:
            out.gate_counts[G_REFRACTORY] += 1
            out.fired_ticks.append((latest_ts, "refractory"))
        else:
            out.gate_counts[G_ALERT] += 1
            out.alerts.append(det)
            out.fired_ticks.append((latest_ts, "alert"))
            last_alert = t
        t += step
    return out


def event_run_lengths(fired_ticks: list[tuple[datetime, str]], step_min: int) -> list[int]:
    """Lengths (in ticks) of consecutive-tick runs of fired candidates.

    A long run means one rise stays detectable for several cycles, so a brief
    feed outage would NOT make the loop miss it.
    """
    runs, cur, prev = [], 0, None
    for ts, _ in fired_ticks:
        if prev is not None and (ts - prev) <= timedelta(minutes=step_min * 1.5):
            cur += 1
        else:
            if cur:
                runs.append(cur)
            cur = 1
        prev = ts
    if cur:
        runs.append(cur)
    return runs


@dataclass(frozen=True)
class PrecisionSummary:
    total: int
    pre_bolused: int
    late_bolused_before_alert: int
    late_bolused_after_alert: int
    uncovered: int
    uncovered_user_correction: int
    uncovered_auto_correction: int
    uncovered_no_resolution: int

    @property
    def strict(self) -> float:
        return self.uncovered / self.total if self.total else 0.0

    @property
    def alert_time_actionable(self) -> float:
        """Uncovered + late boluses that landed AFTER the alert would have fired."""
        n = self.uncovered + self.late_bolused_after_alert
        return n / self.total if self.total else 0.0


def summarize_precision(scored: list[ScoredInstance]) -> PrecisionSummary:
    c = Counter()
    for s in scored:
        if s.label == LABEL_PRE:
            c["pre"] += 1
        elif s.label == LABEL_LATE:
            # bolus_delay_min is measured from rise_start; alert time == +window.
            # Computed from stored timestamps to avoid assuming the window length.
            before = s.matched_bolus_ts is not None and s.matched_bolus_ts <= s.anchor_ts
            c["late_before" if before else "late_after"] += 1
        else:
            c["unc"] += 1
            c["unc_" + str(s.resolution)] += 1
    return PrecisionSummary(
        total=len(scored), pre_bolused=c["pre"],
        late_bolused_before_alert=c["late_before"],
        late_bolused_after_alert=c["late_after"], uncovered=c["unc"],
        uncovered_user_correction=c["unc_user_correction"],
        uncovered_auto_correction=c["unc_auto_correction"],
        uncovered_no_resolution=c["unc_none"],
    )


def p_zero_given_fresh_fraction(n_events: int, f: float) -> float:
    """P(no alert in ``n_events`` independent events | each seen w.p. f). IID model."""
    return (1.0 - f) ** n_events


# --------------------------------------------------------------------------- #
# I/O (read-only)
# --------------------------------------------------------------------------- #

def resolve_db_url(env_file: str | None) -> str:
    url = None
    if env_file:
        from dotenv import dotenv_values  # parsed only; never exported to os.environ
        url = dotenv_values(env_file).get("SUPABASE_DB_URL")
    url = url or os.environ.get("SUPABASE_DB_URL")
    if not url:
        raise SystemExit("SUPABASE_DB_URL not found (set it or pass --env-file).")
    return url


def load_from_supabase(url: str, start: datetime, end: datetime
                       ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """SELECT-only read of pump CGM + boluses in a READ ONLY session."""
    import psycopg2

    pad_lo = start - timedelta(hours=2)
    pad_hi = end + timedelta(hours=4)  # correction_lookahead resolution window
    conn = psycopg2.connect(url)
    try:
        # NEVER enable autocommit on this connection (neither via set_session
        # nor the conn.autocommit attribute). Without autocommit psycopg2 emits
        # per-transaction BEGIN READ ONLY; WITH autocommit it instead issues a
        # session-level SET default_transaction_read_only = on, which on the
        # transaction-mode pooler leaks onto a shared backend and breaks the
        # live worker's writes (incident 2026-10-14). Enforced by
        # tests/test_no_pooler_session_leak.py.
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT timestamp, bg_mgdl FROM cgm "
                "WHERE timestamp >= %s AND timestamp < %s ORDER BY timestamp",
                (pad_lo, pad_hi))
            cgm = pd.DataFrame(cur.fetchall(), columns=["timestamp", "bg_mgdl"])
            cur.execute(
                "SELECT timestamp, bolus_category::text, carbs_g FROM requests "
                "WHERE timestamp >= %s AND timestamp < %s ORDER BY timestamp",
                (pad_lo, pad_hi))
            req = pd.DataFrame(cur.fetchall(),
                               columns=["timestamp", "bolus_category", "carbs_g"])
        conn.rollback()
    finally:
        conn.close()
    for df in (cgm, req):
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    cgm["bg_mgdl"] = cgm["bg_mgdl"].astype(int)
    return cgm, req


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def _weeks(a: datetime, b: datetime) -> float:
    return max((b - a).total_seconds() / (7 * 86400), 1e-9)


def window_report(name: str, res: ReplayResult, scored: list[ScoredInstance],
                  a: datetime, b: datetime, step_min: int) -> dict:
    wk = _weeks(a, b)
    prec = summarize_precision(scored)
    runs = event_run_lengths(res.fired_ticks, step_min)
    return {
        "window": name, "from": a.date().isoformat(), "to": b.date().isoformat(),
        "weeks": round(wk, 2), "ticks": res.ticks,
        "fresh_tick_fraction": round(sum(res.fresh_ticks_by_month.values()) / max(res.ticks, 1), 3),
        "gate_counts": {g: res.gate_counts.get(g, 0) for g in GATE_ORDER},
        "alerts": len(res.alerts), "alerts_per_week": round(len(res.alerts) / wk, 2),
        "mirror_mismatches": res.mirror_mismatches,
        "slope_ratio_hist_0.1_buckets": {str(k): v for k, v in sorted(res.slope_ratio_hist.items())},
        "event_runs_ticks": {"n_events": len(runs),
                             "median": float(np.median(runs)) if runs else 0.0,
                             "max": max(runs) if runs else 0},
        "precision": dataclasses.asdict(prec) | {
            "strict_uncovered_rate": round(prec.strict, 3),
            "alert_time_actionable_rate": round(prec.alert_time_actionable, 3)},
    }


def run(*, start: str, end: str, live_since: str, env_file: str | None,
        phases: list[float], latency_min: float, step_min: int, out_dir: Path,
        config: AppConfig | None = None,
        frames: tuple[pd.DataFrame, pd.DataFrame] | None = None) -> dict:
    config = config or get_config()
    tz = ZoneInfo(config.timezone)
    a = datetime.fromisoformat(start).replace(tzinfo=tz)
    b = datetime.fromisoformat(end).replace(tzinfo=tz) + timedelta(days=1)
    live = datetime.fromisoformat(live_since).replace(tzinfo=tz)
    if frames is None:
        cgm, req = load_from_supabase(resolve_db_url(env_file), a, b)
    else:
        cgm, req = frames

    reports, all_detail = [], []
    res = None
    for ph in phases:
        for label, lo, hi in (("full", a, b), ("live_period", live, b)):
            res = replay(cgm, config, start=lo, end=hi, step_min=step_min,
                         latency_min=latency_min, phase_min=ph)
            scored = score_instances(res.alerts, req, config.meal_rise_calibration)
            rep = window_report(label, res, scored, lo, hi, step_min)
            rep["phase_min"] = ph
            reports.append(rep)
            if ph == phases[0] and label == "full":
                all_detail = [
                    {"anchor": s.anchor_ts.isoformat(), "label": s.label,
                     "start": s.start_level, "end": s.end_level, "delta": s.delta,
                     "slope": round(s.slope_mgdl_per_min, 3), "hour": s.hour_of_day,
                     "resolution": s.resolution}
                    for s in scored]
                full_res = res
    monthly = {m: {"ticks": full_res.ticks_by_month[m],
                   "fresh_fraction": round(full_res.fresh_ticks_by_month[m] / full_res.ticks_by_month[m], 3)}
               for m in sorted(full_res.ticks_by_month)}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {"config": {
        "base_slope": config.meal_rise.base_slope_mgdl_per_min,
        "off_hours_multiplier": config.meal_rise.off_hours_multiplier,
        "meal_windows": list(config.meal_rise.meal_windows),
        "refractory_minutes": config.meal_rise.refractory_minutes,
        "latency_min": latency_min, "step_min": step_min},
        "reports": reports, "pump_data_fresh_fraction_by_month": monthly}
    (out_dir / f"meal_rise_live_replay_{stamp}.json").write_text(json.dumps(payload, indent=2))
    (out_dir / f"meal_rise_live_replay_detail_{stamp}.json").write_text(json.dumps(all_detail, indent=2))
    return payload


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--start", default="2026-01-01")
    p.add_argument("--end", default="2026-08-16", help="inclusive, config tz")
    p.add_argument("--live-since", default="2026-07-01",
                   help="start of the period the live loop actually ran")
    p.add_argument("--env-file", default=None)
    p.add_argument("--phases", default="0", help="comma list of grid phase offsets (min)")
    p.add_argument("--latency-min", type=float, default=2.0)
    p.add_argument("--step-min", type=int, default=5)
    p.add_argument("--out-dir", default="data/reports", type=Path)
    a = p.parse_args()
    payload = run(start=a.start, end=a.end, live_since=a.live_since,
                  env_file=a.env_file,
                  phases=[float(x) for x in a.phases.split(",")],
                  latency_min=a.latency_min, step_min=a.step_min, out_dir=a.out_dir)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
