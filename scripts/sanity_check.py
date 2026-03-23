"""Sanity check: load parquet files and print a human-readable summary for one day.

Usage: uv run python main.py check --date 2026-03-20
"""

from __future__ import annotations

import json
import sys
from datetime import date

import pandas as pd

from ingestion.storage import load_df


def _filter_day(df: pd.DataFrame | None, target: date, ts_col: str = "timestamp") -> pd.DataFrame:
    """Filter DataFrame to rows matching target date."""
    if df is None or df.empty:
        return pd.DataFrame()
    mask = pd.to_datetime(df[ts_col]).dt.date == target
    return df[mask]


def sanity_check(date_str: str) -> None:
    target = date.fromisoformat(date_str)
    print(f"\n{'='*60}")
    print(f"  SANITY CHECK: {target}")
    print(f"{'='*60}\n")

    # ── CGM ──────────────────────────────────────────────────────
    cgm = _filter_day(load_df("cgm"), target)
    print(f"CGM readings: {len(cgm)}")
    if not cgm.empty:
        bg = cgm["bg_mgdl"]
        tir = ((bg >= 70) & (bg <= 180)).mean() * 100
        print(f"  Mean BG: {bg.mean():.0f} mg/dL")
        print(f"  Min/Max: {bg.min()} / {bg.max()} mg/dL")
        print(f"  Time in range (70-180): {tir:.0f}%")
        coverage = len(cgm) / 288 * 100
        print(f"  Coverage: {coverage:.0f}% ({len(cgm)}/288 expected)")

    # ── Boluses ──────────────────────────────────────────────────
    bolus = _filter_day(load_df("bolus"), target)
    print(f"\nBoluses: {len(bolus)}")
    if not bolus.empty:
        print(f"  Total insulin: {bolus['insulin_units'].sum():.2f} u")
        for _, row in bolus.iterrows():
            ts = pd.to_datetime(row["timestamp"]).strftime("%H:%M")
            print(f"    {ts}  {row['insulin_units']:.2f}u  (id={row['bolus_id']})")

    # ── Meals / bolus requests ───────────────────────────────────
    requests = _filter_day(load_df("requests"), target)
    meals = requests[requests["carbs_g"] > 0] if not requests.empty else pd.DataFrame()
    print(f"\nBolus requests: {len(requests)}  |  Meals (carbs > 0): {len(meals)}")
    if not meals.empty:
        print(f"  Total carbs: {meals['carbs_g'].sum()}g")
        for _, row in meals.iterrows():
            ts = pd.to_datetime(row["timestamp"]).strftime("%H:%M")
            src = row.get("bolus_source", "?")
            print(f"    {ts}  {row['carbs_g']}g  BG={row['bg_mgdl']}  source={src}")

    # ── Basal ────────────────────────────────────────────────────
    basal = _filter_day(load_df("basal"), target)
    print(f"\nBasal entries: {len(basal)}")
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
    suspension = _filter_day(load_df("suspension"), target, ts_col="suspend_timestamp")
    print(f"\nSuspensions: {len(suspension)}")
    if not suspension.empty:
        for _, row in suspension.iterrows():
            ts = pd.to_datetime(row["suspend_timestamp"]).strftime("%H:%M")
            dur = f"{row['duration_minutes']:.0f}min" if pd.notna(row["duration_minutes"]) else "ongoing"
            suspect = " [SUSPECT]" if row.get("pairing_suspect") else ""
            print(f"    {ts}  {dur}  reason={row['suspend_reason']}{suspect}")

    # ── Events (mode changes, site changes) ──────────────────────
    events = _filter_day(load_df("events"), target)
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

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/sanity_check.py YYYY-MM-DD")
        sys.exit(1)
    sanity_check(sys.argv[1])
