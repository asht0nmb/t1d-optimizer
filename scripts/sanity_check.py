"""Sanity check: load parquet files and print a human-readable summary for one day.

Usage:
    uv run python main.py check --date 2026-03-20                 # original view
    uv run python main.py check --date 2026-03-20 --view enriched  # + enrichment

The `--view` flag is a read-only projection:

* ``original`` — load parquets as-is, minus any enrichment columns that
  happen to be present on disk. No extra sections are printed; output
  matches the pre-enrichment behavior byte-for-byte on pre-enrichment data.
* ``enriched`` — backfill `bolus_category` / `override_delta` /
  `forced_by_alarm` / `site_issues` / `cgm_gaps` in memory if absent, then
  print the base sections plus additional enrichment sections.

The underlying parquets are never modified by `check`.
"""

from __future__ import annotations

import sys
from datetime import date
from typing import Iterable

import pandas as pd

from detection.config import get_config
from ingestion.storage import load_df
from ingestion.version_guard import warn_if_stale
from ingestion.view_data import (
    VIEW_MODES,
    ViewMode,
    ensure_enriched,
    strip_enriched_columns,
)

_ALL_FRAMES: tuple[str, ...] = (
    "cgm", "bolus", "requests", "basal", "suspension",
    "events", "alarms", "site_issues", "cgm_gaps",
)


def _filter_day(df: pd.DataFrame | None, target: date, ts_col: str = "timestamp") -> pd.DataFrame:
    """Filter DataFrame to rows matching target date."""
    if df is None or df.empty:
        return pd.DataFrame()
    mask = pd.to_datetime(df[ts_col]).dt.date == target
    return df[mask]


def _overlapping_day(
    df: pd.DataFrame | None,
    target: date,
    start_col: str,
    end_col: str,
) -> pd.DataFrame:
    """Return rows whose [start, end] window touches ``target``.

    Rows where ``end_col`` is NaT (ongoing episode) are treated as extending
    indefinitely and are included when ``start <= end_of_day``.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    starts = pd.to_datetime(df[start_col])
    ends = pd.to_datetime(df[end_col]) if end_col in df.columns else pd.Series(pd.NaT, index=df.index)

    tz = getattr(starts.dt, "tz", None)
    day_start = pd.Timestamp(target)
    day_end = day_start + pd.Timedelta(days=1)
    if tz is not None:
        day_start = day_start.tz_localize(tz)
        day_end = day_end.tz_localize(tz)

    ongoing = ends.isna()
    mask = ((starts < day_end) & (ends >= day_start)) | (ongoing & (starts < day_end))
    return df[mask]


def _load_all_frames(view: ViewMode) -> dict[str, pd.DataFrame]:
    """Assemble the frame dict via the direct `load_df` path.

    Kept in-module (rather than using `ingestion.view_data.load_frames`) so
    tests can continue to patch `scripts.sanity_check.load_df`.
    """
    frames: dict[str, pd.DataFrame] = {}
    for name in _ALL_FRAMES:
        df = load_df(name)
        frames[name] = df if df is not None else pd.DataFrame()

    if view == "enriched":
        config = get_config()
        frames = ensure_enriched(frames, config)
    else:  # original
        for name in list(frames):
            frames[name] = strip_enriched_columns(name, frames[name])
    return frames


def sanity_check(date_str: str, view: ViewMode = "original") -> None:
    if view not in VIEW_MODES:
        raise ValueError(
            f"Unknown view mode {view!r}; expected one of {VIEW_MODES}"
        )

    target = date.fromisoformat(date_str)
    warn_if_stale(stream="stdout")
    print(f"\n{'='*60}")
    print(f"  SANITY CHECK: {target}  [view={view}]")
    print(f"{'='*60}\n")

    frames = _load_all_frames(view)
    # TIR band is config-driven (CLAUDE.md §Critical Rules); reading it here
    # keeps the text summary in lockstep with `daily_viz` and the detection
    # engine, all of which route through `detection.config.get_config`.
    bg_targets = get_config().bg_targets
    low = bg_targets.low
    high = bg_targets.high

    # ── CGM ──────────────────────────────────────────────────────
    cgm = _filter_day(frames.get("cgm"), target)
    print(f"CGM readings: {len(cgm)}")
    if not cgm.empty:
        bg = cgm["bg_mgdl"]
        tir = ((bg >= low) & (bg <= high)).mean() * 100
        print(f"  Mean BG: {bg.mean():.0f} mg/dL")
        print(f"  Min/Max: {bg.min()} / {bg.max()} mg/dL")
        print(f"  Time in range ({low}-{high}): {tir:.0f}%")
        coverage = len(cgm) / 288 * 100
        print(f"  Coverage: {coverage:.0f}% ({len(cgm)}/288 expected)")

    # ── Boluses ──────────────────────────────────────────────────
    bolus = _filter_day(frames.get("bolus"), target)
    print(f"\nBoluses: {len(bolus)}")
    if not bolus.empty:
        print(f"  Total insulin: {bolus['insulin_units'].sum():.2f} u")
        for _, row in bolus.iterrows():
            ts = pd.to_datetime(row["timestamp"]).strftime("%H:%M")
            print(f"    {ts}  {row['insulin_units']:.2f}u  (id={row['bolus_id']})")

    # ── Meals / bolus requests ───────────────────────────────────
    requests = _filter_day(frames.get("requests"), target)
    meals = requests[requests["carbs_g"] > 0] if not requests.empty else pd.DataFrame()
    print(f"\nBolus requests: {len(requests)}  |  Meals (carbs > 0): {len(meals)}")
    if not meals.empty:
        print(f"  Total carbs: {meals['carbs_g'].sum()}g")
        for _, row in meals.iterrows():
            ts = pd.to_datetime(row["timestamp"]).strftime("%H:%M")
            src = row.get("bolus_source", "?")
            print(f"    {ts}  {row['carbs_g']}g  BG={row['bg_mgdl']}  source={src}")

    # ── Basal ────────────────────────────────────────────────────
    basal = _filter_day(frames.get("basal"), target)
    print(f"\nBasal entries: {len(basal)}")
    tdd_basal = 0.0
    if not basal.empty:
        # Each entry covers 5 min; total daily dose = sum(rate * 5/60)
        tdd_basal = (basal["commanded_rate"] * 5 / 60).sum()
        print(f"  Total basal insulin: {tdd_basal:.2f} u")
        sources = basal["rate_source"].value_counts()
        for src, cnt in sources.items():
            print(f"    {src}: {cnt} entries")

    # ── TDD ──────────────────────────────────────────────────────
    if not bolus.empty and not basal.empty:
        tdd_bolus = bolus["insulin_units"].sum()
        tdd = tdd_bolus + tdd_basal
        print(f"\n  Total Daily Dose: {tdd:.2f} u (bolus={tdd_bolus:.2f} + basal={tdd_basal:.2f})")

    # ── Suspensions ──────────────────────────────────────────────
    suspension = _filter_day(frames.get("suspension"), target, ts_col="suspend_timestamp")
    print(f"\nSuspensions: {len(suspension)}")
    if not suspension.empty:
        for _, row in suspension.iterrows():
            ts = pd.to_datetime(row["suspend_timestamp"]).strftime("%H:%M")
            dur = f"{row['duration_minutes']:.0f}min" if pd.notna(row["duration_minutes"]) else "ongoing"
            suspect = " [SUSPECT]" if row.get("pairing_suspect") else ""
            alarm = row.get("alarm_name", None)
            alarm_str = f"  ({alarm})" if alarm and pd.notna(alarm) else ""
            print(f"    {ts}  {dur}  reason={row['suspend_reason']}{alarm_str}{suspect}")

    # ── Events (mode changes, site changes) ──────────────────────
    events = _filter_day(frames.get("events"), target)
    if not events.empty:
        mode_changes = events[events["event_type"] == "mode_change"]
        site_changes = events[events["event_type"] == "site_change"]

        if not mode_changes.empty:
            print(f"\nMode changes: {len(mode_changes)}")
            for _, row in mode_changes.iterrows():
                ts = pd.to_datetime(row["timestamp"]).strftime("%H:%M")
                prev = row.get("previous_mode", "?")
                print(f"    {ts}  {prev} → {row['event_subtype']}")

        if not site_changes.empty:
            print(f"\nSite changes: {len(site_changes)}")
            for _, row in site_changes.iterrows():
                ts = pd.to_datetime(row["timestamp"]).strftime("%H:%M")
                print(f"    {ts}  {row['event_subtype']}")

    # ── Alarms ─────────────────────────────────────────────────
    alarms = _filter_day(frames.get("alarms"), target)
    if not alarms.empty:
        activated = alarms[alarms["action"] == "activated"]
        print(f"\nAlarms/Alerts: {len(activated)} activated ({len(alarms)} total)")
        for _, row in activated.iterrows():
            ts = pd.to_datetime(row["timestamp"]).strftime("%H:%M")
            cat = row["category"]
            name = row["alarm_name"]
            p1 = f"  param1={row['param1']:.0f}" if pd.notna(row.get("param1")) else ""
            p2 = f"  param2={row['param2']:.0f}" if pd.notna(row.get("param2")) else ""
            print(f"    {ts}  [{cat}] {name}{p1}{p2}")

    # ── Enrichment-only sections ─────────────────────────────────
    if view == "enriched":
        _print_enriched_sections(requests, events, frames, target)
        _print_stacking_heuristic(cgm)

    print(f"\n{'='*60}\n")


_STACKING_THRESHOLD = 3


def _print_stacking_heuristic(cgm_day: pd.DataFrame) -> None:
    """Warn if the day's CGM has ≥N readings sharing the exact same second.

    Enriched-only signal: surfaces the pre-v2 backfill bug's fingerprint
    (bursts of backfilled readings collapsing onto the pump-reconnect
    second) from inside a single-day check, even when nobody remembers to
    run `doctor`.
    """
    if cgm_day is None or cgm_day.empty or "timestamp" not in cgm_day.columns:
        return
    timestamps = pd.to_datetime(cgm_day["timestamp"])
    # See scripts/doctor.py — round-trip via UTC so DST-fallback days don't
    # raise an ambiguous-time error inside the heuristic.
    original_tz = timestamps.dt.tz
    if original_tz is not None:
        buckets = (
            timestamps.dt.tz_convert("UTC")
            .dt.floor("1s")
            .dt.tz_convert(original_tz)
        )
    else:
        buckets = timestamps.dt.floor("1s")
    counts = buckets.value_counts()
    stacked = counts[counts >= _STACKING_THRESHOLD]
    if stacked.empty:
        return
    worst_ts = stacked.idxmax()
    worst_n = int(stacked.max())
    print(
        f"\n⚠️  same-second CGM stacking: {len(stacked)} timestamp(s) "
        f"have ≥{_STACKING_THRESHOLD} readings (worst: {worst_n} rows @ "
        f"{worst_ts.strftime('%H:%M:%S')}). Pre-v2 backfill fingerprint — "
        f"regenerate with `uv run python main.py fetch --clean`."
    )


def _print_enriched_sections(
    requests_day: pd.DataFrame,
    events_day: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
    target: date,
) -> None:
    """Print the additional sections the enriched view adds."""
    # ── Bolus categories (requests) ──────────────────────────────
    if not requests_day.empty and "bolus_category" in requests_day.columns:
        print(f"\nBolus categories: {len(requests_day)}")
        cat_counts = requests_day["bolus_category"].value_counts(dropna=False)
        for cat, cnt in cat_counts.items():
            print(f"    {cat}: {cnt}")
        for _, row in requests_day.iterrows():
            ts = pd.to_datetime(row["timestamp"]).strftime("%H:%M")
            cat = row.get("bolus_category", "?")
            src = row.get("bolus_source", "?")
            delta = row.get("override_delta")
            delta_str = ""
            if pd.notna(delta):
                delta_str = f"  override_delta={delta:+.2f}u"
            print(f"    {ts}  source={src}  category={cat}{delta_str}")

    # ── Forced site changes ─────────────────────────────────────
    site_changes = (
        events_day[events_day["event_type"] == "site_change"]
        if not events_day.empty
        else pd.DataFrame()
    )
    if not site_changes.empty and "forced_by_alarm" in site_changes.columns:
        print(f"\nForced site changes (enriched): {len(site_changes)}")
        for _, row in site_changes.iterrows():
            ts = pd.to_datetime(row["timestamp"]).strftime("%H:%M")
            forced = row.get("forced_by_alarm")
            forced_str = "forced=True" if forced is True else "forced=False"
            print(f"    {ts}  {row['event_subtype']}  {forced_str}")

    # ── Site issues overlapping the day ──────────────────────────
    site_issues = frames.get("site_issues")
    overlapping_issues = _overlapping_day(
        site_issues, target,
        start_col="first_occlusion_ts", end_col="last_occlusion_ts",
    )
    print(f"\nSite issues overlapping day: {len(overlapping_issues)}")
    if not overlapping_issues.empty:
        for _, row in overlapping_issues.iterrows():
            first = pd.to_datetime(row["first_occlusion_ts"]).strftime("%Y-%m-%d %H:%M")
            last = pd.to_datetime(row["last_occlusion_ts"]).strftime("%H:%M")
            cnt = int(row["occlusion_count"])
            resolver = row.get("resolved_by_site_change_ts")
            resolver_str = (
                pd.to_datetime(resolver).strftime("%Y-%m-%d %H:%M")
                if pd.notna(resolver) else "unresolved"
            )
            delay = row.get("resolution_delay_minutes")
            delay_str = f"  delay={delay:.0f}min" if pd.notna(delay) else ""
            print(
                f"    {first} → {last}  occlusions={cnt}  "
                f"resolved_at={resolver_str}{delay_str}"
            )

    # ── CGM gaps overlapping the day ─────────────────────────────
    cgm_gaps = frames.get("cgm_gaps")
    overlapping_gaps = _overlapping_day(
        cgm_gaps, target,
        start_col="start_ts", end_col="end_ts",
    )
    print(f"\nCGM gaps overlapping day: {len(overlapping_gaps)}")
    if not overlapping_gaps.empty:
        for _, row in overlapping_gaps.iterrows():
            start = pd.to_datetime(row["start_ts"]).strftime("%Y-%m-%d %H:%M")
            end_ts = row.get("end_ts")
            end = pd.to_datetime(end_ts).strftime("%H:%M") if pd.notna(end_ts) else "ongoing"
            dur = row.get("duration_minutes")
            dur_str = f"{dur:.0f}min" if pd.notna(dur) else "ongoing"
            print(f"    {start} → {end}  duration={dur_str}")


if __name__ == "__main__":
    args: Iterable[str] = sys.argv[1:]
    if not args:
        print("Usage: python scripts/sanity_check.py YYYY-MM-DD [--view original|enriched]")
        sys.exit(1)
    args_list = list(args)
    view: ViewMode = "original"
    if "--view" in args_list:
        i = args_list.index("--view")
        view = args_list[i + 1]  # type: ignore[assignment]
        del args_list[i:i + 2]
    sanity_check(args_list[0], view=view)
