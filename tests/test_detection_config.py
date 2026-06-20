"""Tests for detection/config.py — typed AppConfig loader."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest
import yaml

from detection.config import CONFIG_PATH, AppConfig, get_config, load_config


_VALID_CONFIG_YAML = textwrap.dedent(
    """
    ingestion:
      timezone: "America/Los_Angeles"
      chunk_days: 30

    bg_targets:
      low: 70
      high: 180
      target: 110

    meal_detection:
      rise_threshold_per_5min: 8
      sustained_intervals: 3
      no_bolus_window_minutes: 30
      meal_windows:
        - [6, 10]
        - [11, 14]
        - [17, 23]

    anomaly_detection:
      spike_threshold: 180
      drop_threshold: 70
      flatline_tolerance: 2
      flatline_consecutive_intervals: 6

    clustering:
      method: kmeans
      n_clusters: 5
      feature_mode: aggregated
      random_seed: 7
      model_dir: custom/models

    site_change_detection:
      forced_window_minutes: 120
      cartridge_real_fill_threshold: 220
      occlusion_cluster_window_minutes: 180
      min_occlusions_for_cluster: 2

    meal_rise:
      window_minutes: 30
      fetch_buffer_minutes: 15
      expected_interval_minutes: 5
      fetch_readings_padding: 3
      min_samples: 4
      min_coverage: 0.7
      base_slope_mgdl_per_min: 1.8
      start_level_min: 70
      start_level_max: 250
      meal_windows:
        - {start_hour: 6,  end_hour: 10, multiplier: 0.7}
        - {start_hour: 11, end_hour: 14, multiplier: 0.7}
        - {start_hour: 17, end_hour: 21, multiplier: 0.7}
      off_hours_multiplier: 1.3
      refractory_minutes: 60
      alert_template: "Fast glucose rise"
    """
).strip()


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "cfg.yaml"
    p.write_text(body)
    return p


class TestLoadConfig:
    def test_valid_config_loads(self):
        # Load the checked-in config directly (no path arg).
        cfg = load_config()
        assert isinstance(cfg, AppConfig)

        assert cfg.bg_targets.low == 70
        assert cfg.bg_targets.target == 110
        assert cfg.bg_targets.high == 180

        assert cfg.meal_detection.rise_threshold_per_5min == 8
        assert cfg.meal_detection.sustained_intervals == 3
        assert cfg.meal_detection.no_bolus_window_minutes == 30
        assert cfg.meal_detection.meal_windows == ((6, 10), (11, 14), (17, 23))

        assert cfg.anomaly_detection.spike_threshold == 180
        assert cfg.anomaly_detection.drop_threshold == 70
        assert cfg.anomaly_detection.flatline_tolerance == 2
        # Not in checked-in YAML yet → default 12 (Task 2.2 will add it).
        assert cfg.anomaly_detection.flatline_consecutive_intervals == 12

        assert cfg.clustering.method == "kmeans"
        assert cfg.clustering.n_clusters == 5
        assert cfg.clustering.feature_mode == "aggregated"
        assert cfg.clustering.random_seed == 42
        assert cfg.clustering.model_dir == "data/models"

        assert cfg.site_change_detection.forced_window_minutes == 120
        assert cfg.site_change_detection.cartridge_real_fill_threshold == 220
        assert cfg.site_change_detection.occlusion_cluster_window_minutes == 180
        assert cfg.site_change_detection.min_occlusions_for_cluster == 2

        assert cfg.meal_rise.window_minutes == 30
        assert cfg.meal_rise.min_samples == 4
        assert cfg.meal_rise.min_coverage == 0.7
        assert cfg.meal_rise.base_slope_mgdl_per_min == 1.8
        assert cfg.meal_rise.start_level_min == 70
        assert cfg.meal_rise.start_level_max == 250
        assert len(cfg.meal_rise.meal_windows) == 3
        assert cfg.meal_rise.off_hours_multiplier == 1.3
        assert cfg.meal_rise.refractory_minutes == 60
        assert cfg.meal_rise.fetch_buffer_minutes == 15
        assert cfg.meal_rise.expected_interval_minutes == 5
        assert cfg.meal_rise.fetch_readings_padding == 3
        assert cfg.meal_rise.max_reading_age_minutes == 15
        assert "Fast glucose rise" in cfg.meal_rise.alert_template

        assert cfg.meal_rise_calibration.pre_bolus_lookback_minutes == 30
        assert cfg.meal_rise_calibration.late_bolus_lookahead_minutes == 45
        assert cfg.meal_rise_calibration.correction_lookahead_minutes == 180

        assert cfg.timezone == "America/Los_Angeles"
        assert isinstance(cfg.raw, dict)
        assert cfg.raw["bg_targets"]["target"] == 110

    def test_load_from_tmp_path(self, tmp_path):
        p = _write(tmp_path, _VALID_CONFIG_YAML)
        cfg = load_config(p)
        assert cfg.bg_targets.target == 110
        assert cfg.anomaly_detection.flatline_consecutive_intervals == 6
        assert cfg.clustering.random_seed == 7
        assert cfg.clustering.model_dir == "custom/models"
        assert cfg.timezone == "America/Los_Angeles"

    def test_meal_rise_max_reading_age_defaults_when_absent(self, tmp_path):
        # _VALID_CONFIG_YAML omits max_reading_age_minutes → falls back to default.
        cfg = load_config(_write(tmp_path, _VALID_CONFIG_YAML))
        assert cfg.meal_rise.max_reading_age_minutes == 15

    def test_meal_rise_max_reading_age_must_be_positive(self, tmp_path):
        body = _VALID_CONFIG_YAML.replace(
            "refractory_minutes: 60",
            "refractory_minutes: 60\n  max_reading_age_minutes: 0",
        )
        with pytest.raises(ValueError, match="max_reading_age_minutes"):
            load_config(_write(tmp_path, body))

    def test_meal_rise_calibration_defaults_when_absent(self, tmp_path):
        # _VALID_CONFIG_YAML omits the meal_rise_calibration block → defaults.
        cfg = load_config(_write(tmp_path, _VALID_CONFIG_YAML))
        assert cfg.meal_rise_calibration.pre_bolus_lookback_minutes == 30
        assert cfg.meal_rise_calibration.late_bolus_lookahead_minutes == 45
        assert cfg.meal_rise_calibration.correction_lookahead_minutes == 180

    def test_meal_rise_calibration_must_be_positive(self, tmp_path):
        body = _VALID_CONFIG_YAML.rstrip() + (
            "\n\nmeal_rise_calibration:\n"
            "  pre_bolus_lookback_minutes: 0\n"
            "  late_bolus_lookahead_minutes: 45\n"
            "  correction_lookahead_minutes: 180\n"
        )
        with pytest.raises(ValueError, match="meal_rise_calibration"):
            load_config(_write(tmp_path, body))

    def test_missing_top_level_key_raises(self, tmp_path):
        # Drop meal_detection entirely.
        body = textwrap.dedent(
            """
            ingestion: {timezone: "UTC"}
            bg_targets: {low: 70, high: 180, target: 110}
            anomaly_detection: {spike_threshold: 180, drop_threshold: 70, flatline_tolerance: 2}
            clustering: {method: kmeans, n_clusters: 5, feature_mode: aggregated}
            site_change_detection:
              forced_window_minutes: 120
              cartridge_real_fill_threshold: 220
              occlusion_cluster_window_minutes: 180
              min_occlusions_for_cluster: 2
            meal_rise:
              window_minutes: 30
              min_samples: 4
              min_coverage: 0.7
              base_slope_mgdl_per_min: 1.8
              start_level_min: 70
              start_level_max: 250
              meal_windows: []
              off_hours_multiplier: 1.3
              refractory_minutes: 60
              alert_template: "Alert"
            """
        ).strip()
        p = _write(tmp_path, body)
        with pytest.raises(KeyError, match="meal_detection"):
            load_config(p)


    def test_invalid_bg_targets_ordering(self, tmp_path):
        # target > high
        bad = _VALID_CONFIG_YAML.replace("target: 110", "target: 200")
        p = _write(tmp_path, bad)
        with pytest.raises(ValueError, match="bg_targets"):
            load_config(p)

    def test_drop_threshold_not_below_spike(self, tmp_path):
        # drop_threshold >= spike_threshold
        bad = _VALID_CONFIG_YAML.replace("drop_threshold: 70", "drop_threshold: 200")
        p = _write(tmp_path, bad)
        with pytest.raises(ValueError):
            load_config(p)

    def test_n_clusters_below_2_invalid(self, tmp_path):
        bad = _VALID_CONFIG_YAML.replace("n_clusters: 5", "n_clusters: 1")
        p = _write(tmp_path, bad)
        with pytest.raises(ValueError):
            load_config(p)

    def test_meal_windows_invalid_pair_raises(self, tmp_path):
        # start >= end
        bad = _VALID_CONFIG_YAML.replace("- [6, 10]", "- [10, 6]")
        p = _write(tmp_path, bad)
        with pytest.raises(ValueError):
            load_config(p)

    def test_meal_windows_out_of_range_raises(self, tmp_path):
        # end > 24
        bad = _VALID_CONFIG_YAML.replace("- [17, 23]", "- [0, 25]")
        p = _write(tmp_path, bad)
        with pytest.raises(ValueError):
            load_config(p)

    def test_meal_rise_invalid_coverage_raises(self, tmp_path):
        bad = _VALID_CONFIG_YAML.replace("min_coverage: 0.7", "min_coverage: 1.5")
        p = _write(tmp_path, bad)
        with pytest.raises(ValueError, match="min_coverage"):
            load_config(p)

    def test_meal_rise_invalid_start_levels_raises(self, tmp_path):
        bad = _VALID_CONFIG_YAML.replace("start_level_min: 70", "start_level_min: 300")
        p = _write(tmp_path, bad)
        with pytest.raises(ValueError, match="start_level_min"):
            load_config(p)

    def test_meal_rise_meal_windows_dict_missing_key_raises(self, tmp_path):
        bad = _VALID_CONFIG_YAML.replace(
            "- {start_hour: 6,  end_hour: 10, multiplier: 0.7}",
            "- {start_hour: 6, end_hour: 10}",
        )
        p = _write(tmp_path, bad)
        with pytest.raises(KeyError, match="multiplier"):
            load_config(p)

    def test_flatline_consecutive_intervals_default_when_missing(self, tmp_path):
        # Build a yaml without the key under anomaly_detection.
        body = _VALID_CONFIG_YAML.replace(
            "  flatline_consecutive_intervals: 6\n", ""
        )
        assert "flatline_consecutive_intervals" not in body
        p = _write(tmp_path, body)
        cfg = load_config(p)
        assert cfg.anomaly_detection.flatline_consecutive_intervals == 12

    def test_flatline_consecutive_intervals_below_2_invalid(self, tmp_path):
        bad = _VALID_CONFIG_YAML.replace(
            "flatline_consecutive_intervals: 6",
            "flatline_consecutive_intervals: 1",
        )
        p = _write(tmp_path, bad)
        with pytest.raises(ValueError):
            load_config(p)

    def test_clustering_defaults_filled_when_missing(self, tmp_path):
        body = _VALID_CONFIG_YAML
        body = body.replace("  random_seed: 7\n", "")
        body = body.replace("  model_dir: custom/models\n", "")
        assert "random_seed" not in body
        assert "model_dir" not in body
        p = _write(tmp_path, body)
        cfg = load_config(p)
        assert cfg.clustering.random_seed == 42
        assert cfg.clustering.model_dir == "data/models"

    def test_timezone_sourced_from_ingestion_block(self, tmp_path):
        cfg = load_config()
        assert cfg.timezone == "America/Los_Angeles"

    def test_config_path_resolves_from_repo_root(self, tmp_path, monkeypatch):
        assert CONFIG_PATH.is_absolute()
        assert CONFIG_PATH.name == "user_config.yaml"
        assert CONFIG_PATH.parent.name == "config"

        monkeypatch.chdir(tmp_path)
        cfg = load_config(CONFIG_PATH)
        assert cfg.meal_rise.window_minutes == 30

    def test_get_config_caches(self, monkeypatch):
        """get_config() should call yaml.safe_load only once across calls."""
        from detection import config as cfg_module

        get_config.cache_clear()

        call_count = {"n": 0}
        real_safe_load = yaml.safe_load

        def counting_safe_load(stream):
            call_count["n"] += 1
            return real_safe_load(stream)

        monkeypatch.setattr(cfg_module.yaml, "safe_load", counting_safe_load)

        try:
            a = get_config()
            b = get_config()
            assert a is b
            assert call_count["n"] == 1
        finally:
            get_config.cache_clear()
