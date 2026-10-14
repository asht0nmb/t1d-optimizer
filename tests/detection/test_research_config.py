"""research_detection config block: values flow from YAML into behavior."""

from pathlib import Path

import pandas as pd
import yaml

from detection.config import CONFIG_PATH, load_config
from detection.deviation_categorize import CategorizeConfig, categorize_deviations
from detection.iob import IobCurveConfig, compute_iob_activity


def _write(tmp_path: Path, mutate) -> Path:
    raw = yaml.safe_load(CONFIG_PATH.read_text())
    mutate(raw)
    p = tmp_path / "cfg.yaml"
    p.write_text(yaml.safe_dump(raw))
    return p


def test_checked_in_yaml_matches_dataclass_defaults():
    rd = load_config().research_detection
    assert rd.iob == IobCurveConfig()
    assert rd.deviation_categorize == CategorizeConfig()


def test_absent_block_uses_defaults(tmp_path):
    p = _write(tmp_path, lambda r: r.pop("research_detection"))
    rd = load_config(p).research_detection
    assert rd.iob == IobCurveConfig()
    assert rd.deviation_categorize == CategorizeConfig()


def test_noise_band_from_config_changes_categorization(tmp_path):
    df = pd.DataFrame(
        {"timestamp": pd.to_datetime(["2026-01-01 12:00"], utc=True), "deviation_5m": [8.0]}
    )
    empty = pd.DataFrame()
    default = load_config().research_detection.deviation_categorize
    assert categorize_deviations(df, empty, empty, default)["deviation_category"][0] == "unexplained_rise"
    p = _write(tmp_path, lambda r: r["research_detection"]["deviation_categorize"].update(noise_band_mgdl=10.0))
    wide = load_config(p).research_detection.deviation_categorize
    assert wide.noise_band_mgdl == 10.0
    assert categorize_deviations(df, empty, empty, wide)["deviation_category"][0] == "baseline"


def test_dia_from_config_changes_iob(tmp_path):
    t0 = pd.Timestamp("2026-01-01 00:00", tz="UTC")
    doses = pd.DataFrame({"timestamp": [t0], "units": [1.0], "kind": ["bolus"]})
    ev = pd.DatetimeIndex([t0 + pd.Timedelta(hours=4)])
    start = t0 - pd.Timedelta(hours=10)
    base = load_config().research_detection.iob
    p = _write(tmp_path, lambda r: r["research_detection"]["iob"].update(dia_hours=6.0))
    longer = load_config(p).research_detection.iob
    assert longer.dia_hours == 6.0
    a = compute_iob_activity(ev, doses, base, start)["iob"].iloc[0]
    b = compute_iob_activity(ev, doses, longer, start)["iob"].iloc[0]
    assert b > a
