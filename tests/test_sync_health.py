"""Tests for ingestion/sync_health.py — staleness alerts + Telegram sender."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
import yaml

from ingestion import redaction, sync_health

UTC = timezone.utc
NOW = datetime(2026, 10, 14, 6, 0, tzinfo=UTC)
_REPO = Path(__file__).resolve().parents[1]


class TestSyncHealthConfig:
    def test_defaults_when_block_absent(self):
        cfg = sync_health.SyncHealthConfig.from_raw({})
        assert cfg.max_pump_upload_age_hours == 24
        assert cfg.max_pump_cgm_gap_hours == 6

    def test_defaults_when_raw_is_none(self):
        assert sync_health.SyncHealthConfig.from_raw(None) == sync_health.SyncHealthConfig()

    def test_reads_values(self):
        cfg = sync_health.SyncHealthConfig.from_raw(
            {"sync_health": {"max_pump_upload_age_hours": 12, "max_pump_cgm_gap_hours": 3.5}}
        )
        assert cfg.max_pump_upload_age_hours == 12
        assert cfg.max_pump_cgm_gap_hours == 3.5

    @pytest.mark.parametrize("bad", [0, -1, "abc", None])
    def test_rejects_non_positive_or_non_numeric(self, bad):
        with pytest.raises(ValueError):
            sync_health.SyncHealthConfig.from_raw(
                {"sync_health": {"max_pump_upload_age_hours": bad}}
            )

    def test_checked_in_yaml_has_block_and_parses(self):
        text = (_REPO / "config/user_config.yaml").read_text()
        raw = yaml.safe_load(text)
        assert "sync_health" in raw
        cfg = sync_health.SyncHealthConfig.from_raw(raw)
        assert cfg.max_pump_upload_age_hours == 24
        assert cfg.max_pump_cgm_gap_hours == 6


class TestAppConfigTyped:
    def test_app_config_exposes_sync_health(self):
        from detection.config import load_config

        cfg = load_config()
        assert cfg.sync_health.max_pump_upload_age_hours == 24
        assert cfg.sync_health.max_pump_cgm_gap_hours == 6

    def test_missing_block_defaults_in_app_config(self, tmp_path):
        from detection.config import CONFIG_PATH, load_config

        raw = yaml.safe_load(CONFIG_PATH.read_text())
        raw.pop("sync_health")
        p = tmp_path / "cfg.yaml"
        p.write_text(yaml.safe_dump(raw))
        assert load_config(p).sync_health == sync_health.SyncHealthConfig()

    def test_invalid_value_rejected_by_loader(self, tmp_path):
        from detection.config import CONFIG_PATH, load_config

        raw = yaml.safe_load(CONFIG_PATH.read_text())
        raw["sync_health"] = {"max_pump_cgm_gap_hours": -3}
        p = tmp_path / "cfg.yaml"
        p.write_text(yaml.safe_dump(raw))
        with pytest.raises(ValueError):
            load_config(p)


class TestParseUploadTime:
    @pytest.mark.parametrize(
        "value",
        ["2026-10-13T05:00:00Z", "2026-10-13T05:00:00+00:00", "2026-10-13T05:00:00", "2026-10-13T05:00:00.123Z"],
    )
    def test_iso_variants_are_utc(self, value):
        got = sync_health.parse_upload_time(value)
        assert got is not None and got.tzinfo is not None
        assert got.utcoffset() == timedelta(0)
        assert (got.year, got.month, got.day, got.hour) == (2026, 10, 13, 5)

    def test_offset_converted(self):
        got = sync_health.parse_upload_time("2026-10-13T05:00:00-07:00")
        assert got.astimezone(UTC).hour == 12

    @pytest.mark.parametrize("value", [None, "", "garbage", 12])
    def test_unparseable_is_none(self, value):
        assert sync_health.parse_upload_time(value) is None


class TestUploadStaleness:
    def test_fresh_no_alert(self):
        last = (NOW - timedelta(hours=3)).isoformat()
        assert sync_health.check_upload_staleness(last, NOW, 24) is None

    def test_stale_alerts_with_hours(self):
        last = (NOW - timedelta(hours=50)).isoformat()
        alert = sync_health.check_upload_staleness(last, NOW, 24)
        assert alert is not None
        assert alert.kind == "upload_stale"
        assert "~50h" in alert.message
        assert "t:connect app" in alert.message

    def test_boundary_exactly_threshold_alerts(self):
        last = (NOW - timedelta(hours=24)).isoformat()
        assert sync_health.check_upload_staleness(last, NOW, 24) is not None

    def test_missing_or_unparseable_is_unknown_no_alert(self):
        assert sync_health.check_upload_staleness(None, NOW, 24) is None
        assert sync_health.check_upload_staleness("???", NOW, 24) is None

    def test_future_timestamp_no_alert(self):
        last = (NOW + timedelta(hours=2)).isoformat()
        assert sync_health.check_upload_staleness(last, NOW, 24) is None


def _ts(hours_ago: float) -> datetime:
    return datetime(2026, 10, 14, 0, 0, tzinfo=UTC) - timedelta(hours=hours_ago)


def _basal(*hours_ago: float) -> pd.DataFrame:
    return pd.DataFrame({"timestamp": [_ts(h) for h in hours_ago], "rate": 0.8})


def _cgm(*hours_ago: float) -> pd.DataFrame:
    return pd.DataFrame({"timestamp": [_ts(h) for h in hours_ago], "glucose": 120})


class TestPumpCgmGap:
    def test_no_basal_no_alert(self):
        assert sync_health.check_pump_cgm_gap(_cgm(1), pd.DataFrame(), 6) is None

    def test_zero_cgm_over_long_basal_span_alerts(self):
        alert = sync_health.check_pump_cgm_gap(pd.DataFrame(), _basal(30, 20, 10, 0), 6)
        assert alert is not None
        assert alert.kind == "pump_cgm_gap"
        assert "~30h" in alert.message
        assert "G7" in alert.message

    def test_zero_cgm_short_span_no_alert(self):
        assert sync_health.check_pump_cgm_gap(pd.DataFrame(), _basal(3, 2, 0), 6) is None

    def test_trailing_gap_after_last_cgm_alerts(self):
        # CGM stopped 12h before the newest basal delivery
        alert = sync_health.check_pump_cgm_gap(_cgm(40, 30, 12), _basal(40, 20, 0), 6)
        assert alert is not None
        assert "~12h" in alert.message

    def test_healthy_cgm_no_alert(self):
        assert sync_health.check_pump_cgm_gap(_cgm(5, 3, 0.1), _basal(5, 3, 0), 6) is None

    def test_old_resolved_gap_ignored(self):
        # CGM gap in the middle, but readings resume and run to the end.
        cgm = _cgm(60, 59, 1, 0.5, 0)
        assert sync_health.check_pump_cgm_gap(cgm, _basal(60, 30, 0), 6) is None

    def test_cgm_older_than_basal_window_uses_basal_span(self):
        alert = sync_health.check_pump_cgm_gap(_cgm(200), _basal(10, 0), 6)
        assert alert is not None
        assert "~10h" in alert.message


class TestMessagesHaveNoIdentifiers:
    def test_no_serial_uuid_or_name(self):
        a = sync_health.check_upload_staleness((NOW - timedelta(hours=50)).isoformat(), NOW, 24)
        b = sync_health.check_pump_cgm_gap(pd.DataFrame(), _basal(30, 0), 6)
        for alert in (a, b):
            assert redaction.redact(alert.message) == alert.message


class TestSendTelegram:
    def test_missing_creds_returns_false_without_network(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        post = MagicMock()
        monkeypatch.setattr(sync_health.requests, "post", post)
        assert sync_health.send_telegram("hi", config={}) is False
        post.assert_not_called()

    def test_sends_with_env_creds(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:AAEfakefakefakefakefakefakefake1234")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
        resp = MagicMock(status_code=200)
        resp.raise_for_status.return_value = None
        post = MagicMock(return_value=resp)
        monkeypatch.setattr(sync_health.requests, "post", post)
        assert sync_health.send_telegram("hello", config={}) is True
        url = post.call_args.args[0]
        assert url.endswith("/sendMessage")
        assert post.call_args.kwargs["json"] == {"chat_id": "42", "text": "hello"}

    def test_falls_back_to_config_creds(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        post = MagicMock(return_value=resp)
        monkeypatch.setattr(sync_health.requests, "post", post)
        cfg = {"notifications": {"telegram_bot_token": "tok123456789", "telegram_chat_id": "7"}}
        assert sync_health.send_telegram("x", config=cfg) is True

    def test_failure_returns_false_and_logs_type_only(self, monkeypatch, caplog):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:AAEfakefakefakefakefakefakefake1234")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

        def boom(*a, **k):
            raise sync_health.requests.ConnectionError(
                "https://api.telegram.org/bot123456789:AAEfakefakefakefakefakefakefake1234/sendMessage"
            )

        monkeypatch.setattr(sync_health.requests, "post", boom)
        with caplog.at_level("INFO"):
            assert sync_health.send_telegram("x", config={}) is False
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "AAEfake" not in text
        assert "ConnectionError" in text

    def test_http_error_returns_false(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok123456789")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
        resp = MagicMock()
        resp.raise_for_status.side_effect = sync_health.requests.HTTPError("403")
        monkeypatch.setattr(sync_health.requests, "post", MagicMock(return_value=resp))
        assert sync_health.send_telegram("x", config={}) is False
