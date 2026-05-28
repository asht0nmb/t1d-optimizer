"""Typed, validated, cached config loader.

Every detection module reads config through `get_config()`. Validation is
defense-in-depth: missing top-level keys raise `KeyError` naming the key;
ordering / range invariants raise `ValueError` with a clear message.

The loader intentionally does **not** import from `ingestion/` to avoid a
circular dependency — `ingestion.enrich.load_config` delegates here via
`AppConfig.raw` so the fetch pipeline keeps its existing dict-based
contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "AppConfig",
    "AnomalyDetectionConfig",
    "BgTargets",
    "ClusteringConfig",
    "CONFIG_PATH",
    "MealDetectionConfig",
    "MealRiseConfig",
    "SiteChangeDetectionConfig",
    "get_config",
    "load_config",
]

_REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = _REPO_ROOT / "config/user_config.yaml"

_REQUIRED_TOP_LEVEL = (
    "ingestion",
    "bg_targets",
    "meal_detection",
    "anomaly_detection",
    "clustering",
    "site_change_detection",
    "meal_rise",
)

# Default for anomaly_detection.flatline_consecutive_intervals when the key is
# absent. K=12 five-minute intervals = 1 hour of flatlined CGM before we flag.
# Task 2.2 will add this key to the checked-in YAML and make it required.
_FLATLINE_CONSECUTIVE_DEFAULT = 12

# Defaults for clustering block when the keys are absent.
_CLUSTERING_RANDOM_SEED_DEFAULT = 42
_CLUSTERING_MODEL_DIR_DEFAULT = "data/models"


@dataclass(frozen=True)
class BgTargets:
    low: int
    high: int
    target: int


@dataclass(frozen=True)
class MealDetectionConfig:
    rise_threshold_per_5min: float
    sustained_intervals: int
    no_bolus_window_minutes: int
    meal_windows: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class AnomalyDetectionConfig:
    spike_threshold: float
    drop_threshold: float
    flatline_tolerance: float
    flatline_consecutive_intervals: int


@dataclass(frozen=True)
class ClusteringConfig:
    method: str
    n_clusters: int
    feature_mode: str
    random_seed: int
    model_dir: str


@dataclass(frozen=True)
class SiteChangeDetectionConfig:
    forced_window_minutes: int
    occlusion_cluster_window_minutes: int
    min_occlusions_for_cluster: int
    cartridge_real_fill_threshold: int


from core.detection.meal_rise import MealRiseConfig


@dataclass(frozen=True)
class AppConfig:
    bg_targets: BgTargets
    meal_detection: MealDetectionConfig
    anomaly_detection: AnomalyDetectionConfig
    clustering: ClusteringConfig
    site_change_detection: SiteChangeDetectionConfig
    meal_rise: MealRiseConfig
    timezone: str
    raw: dict


def load_config(path: Path | None = None) -> AppConfig:
    """Load, validate, and return a typed `AppConfig`.

    Raises:
        KeyError: a required top-level block is missing (names the key).
        ValueError: a validated invariant is violated (e.g. bg_targets
            ordering, drop < spike, n_clusters >= 2, meal window shape,
            flatline_consecutive_intervals >= 2).
    """
    p = path or CONFIG_PATH
    with open(p) as f:
        raw: dict = yaml.safe_load(f) or {}

    for key in _REQUIRED_TOP_LEVEL:
        if key not in raw:
            raise KeyError(key)

    bg_targets = _parse_bg_targets(raw["bg_targets"])
    meal_detection = _parse_meal_detection(raw["meal_detection"])
    anomaly_detection = _parse_anomaly_detection(raw["anomaly_detection"])
    clustering = _parse_clustering(raw["clustering"])
    site_change_detection = _parse_site_change_detection(raw["site_change_detection"])
    meal_rise = _parse_meal_rise(raw["meal_rise"])
    timezone = _parse_timezone(raw["ingestion"])

    return AppConfig(
        bg_targets=bg_targets,
        meal_detection=meal_detection,
        anomaly_detection=anomaly_detection,
        clustering=clustering,
        site_change_detection=site_change_detection,
        meal_rise=meal_rise,
        timezone=timezone,
        raw=raw,
    )


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Process-wide cached `AppConfig` loaded from `CONFIG_PATH`.

    Call `get_config.cache_clear()` in tests that mutate the underlying
    YAML or monkeypatch `yaml.safe_load`.
    """
    return load_config()


# ---------------------------------------------------------------------------
# Per-block parsers / validators
# ---------------------------------------------------------------------------

def _parse_bg_targets(block: dict[str, Any]) -> BgTargets:
    low = int(block["low"])
    high = int(block["high"])
    target = int(block["target"])
    if not (low < target < high):
        raise ValueError(
            f"bg_targets: require low < target < high, got "
            f"low={low}, target={target}, high={high}"
        )
    return BgTargets(low=low, high=high, target=target)


def _parse_meal_detection(block: dict[str, Any]) -> MealDetectionConfig:
    windows_raw = block["meal_windows"]
    windows: list[tuple[int, int]] = []
    for pair in windows_raw:
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            raise ValueError(
                f"meal_detection.meal_windows: each entry must be a "
                f"[start, end] pair, got {pair!r}"
            )
        start, end = int(pair[0]), int(pair[1])
        if not (0 <= start < end <= 24):
            raise ValueError(
                f"meal_detection.meal_windows: require 0 <= start < end <= 24, "
                f"got [{start}, {end}]"
            )
        windows.append((start, end))

    return MealDetectionConfig(
        rise_threshold_per_5min=float(block["rise_threshold_per_5min"]),
        sustained_intervals=int(block["sustained_intervals"]),
        no_bolus_window_minutes=int(block["no_bolus_window_minutes"]),
        meal_windows=tuple(windows),
    )


def _parse_anomaly_detection(block: dict[str, Any]) -> AnomalyDetectionConfig:
    spike = float(block["spike_threshold"])
    drop = float(block["drop_threshold"])
    if not (drop < spike):
        raise ValueError(
            f"anomaly_detection: require drop_threshold < spike_threshold, "
            f"got drop={drop}, spike={spike}"
        )

    flatline_consec = block.get(
        "flatline_consecutive_intervals", _FLATLINE_CONSECUTIVE_DEFAULT
    )
    flatline_consec = int(flatline_consec)
    if flatline_consec < 2:
        raise ValueError(
            f"anomaly_detection.flatline_consecutive_intervals: must be >= 2, "
            f"got {flatline_consec}"
        )

    return AnomalyDetectionConfig(
        spike_threshold=spike,
        drop_threshold=drop,
        flatline_tolerance=float(block["flatline_tolerance"]),
        flatline_consecutive_intervals=flatline_consec,
    )


def _parse_clustering(block: dict[str, Any]) -> ClusteringConfig:
    n_clusters = int(block["n_clusters"])
    if n_clusters < 2:
        raise ValueError(
            f"clustering.n_clusters: must be >= 2, got {n_clusters}"
        )
    return ClusteringConfig(
        method=str(block["method"]),
        n_clusters=n_clusters,
        feature_mode=str(block["feature_mode"]),
        random_seed=int(block.get("random_seed", _CLUSTERING_RANDOM_SEED_DEFAULT)),
        model_dir=str(block.get("model_dir", _CLUSTERING_MODEL_DIR_DEFAULT)),
    )


def _parse_site_change_detection(block: dict[str, Any]) -> SiteChangeDetectionConfig:
    return SiteChangeDetectionConfig(
        forced_window_minutes=int(block["forced_window_minutes"]),
        occlusion_cluster_window_minutes=int(block["occlusion_cluster_window_minutes"]),
        min_occlusions_for_cluster=int(block["min_occlusions_for_cluster"]),
        cartridge_real_fill_threshold=int(block["cartridge_real_fill_threshold"]),
    )


def _parse_meal_rise(block: dict[str, Any]) -> MealRiseConfig:
    windows_raw = block["meal_windows"]
    windows: list[dict[str, Any]] = []
    for entry in windows_raw:
        if not isinstance(entry, dict):
            raise ValueError(f"meal_rise.meal_windows: entry must be a dict, got {entry!r}")
        required_keys = ("start_hour", "end_hour", "multiplier")
        for k in required_keys:
            if k not in entry:
                raise KeyError(f"meal_rise.meal_windows: missing key {k!r} in {entry!r}")
        start_hour = int(entry["start_hour"])
        end_hour = int(entry["end_hour"])
        multiplier = float(entry["multiplier"])
        if not (0 <= start_hour < end_hour <= 24):
            raise ValueError(f"meal_rise.meal_windows: require 0 <= start_hour < end_hour <= 24, got [{start_hour}, {end_hour}]")
        if multiplier <= 0:
            raise ValueError(f"meal_rise.meal_windows: multiplier must be positive, got {multiplier}")
        windows.append({
            "start_hour": start_hour,
            "end_hour": end_hour,
            "multiplier": multiplier
        })

    window_minutes = int(block["window_minutes"])
    min_samples = int(block["min_samples"])
    min_coverage = float(block["min_coverage"])
    start_level_min = int(block["start_level_min"])
    start_level_max = int(block["start_level_max"])
    refractory_minutes = int(block["refractory_minutes"])
    fetch_buffer_minutes = int(block.get("fetch_buffer_minutes", 15))
    expected_interval_minutes = int(block.get("expected_interval_minutes", 5))
    fetch_readings_padding = int(block.get("fetch_readings_padding", 3))

    if not (0 < min_coverage <= 1.0):
        raise ValueError(
            f"meal_rise.min_coverage: must be in (0, 1], got {min_coverage}"
        )
    if start_level_min >= start_level_max:
        raise ValueError(
            f"meal_rise: require start_level_min < start_level_max, "
            f"got {start_level_min} >= {start_level_max}"
        )
    if refractory_minutes <= 0:
        raise ValueError(
            f"meal_rise.refractory_minutes: must be > 0, got {refractory_minutes}"
        )
    if fetch_buffer_minutes < 0:
        raise ValueError(
            f"meal_rise.fetch_buffer_minutes: must be >= 0, got {fetch_buffer_minutes}"
        )
    if expected_interval_minutes <= 0:
        raise ValueError(
            f"meal_rise.expected_interval_minutes: must be > 0, "
            f"got {expected_interval_minutes}"
        )
    if fetch_readings_padding < 0:
        raise ValueError(
            f"meal_rise.fetch_readings_padding: must be >= 0, "
            f"got {fetch_readings_padding}"
        )

    return MealRiseConfig(
        window_minutes=window_minutes,
        min_samples=min_samples,
        min_coverage=min_coverage,
        base_slope_mgdl_per_min=float(block["base_slope_mgdl_per_min"]),
        start_level_min=start_level_min,
        start_level_max=start_level_max,
        meal_windows=tuple(windows),
        off_hours_multiplier=float(block["off_hours_multiplier"]),
        refractory_minutes=refractory_minutes,
        alert_template=str(block["alert_template"]),
        fetch_buffer_minutes=fetch_buffer_minutes,
        expected_interval_minutes=expected_interval_minutes,
        fetch_readings_padding=fetch_readings_padding,
    )


def _parse_timezone(ingestion_block: dict[str, Any]) -> str:
    tz = ingestion_block.get("timezone")
    if not tz:
        raise KeyError("ingestion.timezone")
    return str(tz)

