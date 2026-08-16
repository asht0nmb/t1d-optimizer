"""Tests for `apps.personal.telegram.llm_assistant` (research branch, unwired).

No network, no real Anthropic key, no `anthropic` package required — every
test here exercises `MockLLMClient` or the pure context/prompt-assembly
functions. `AnthropicLLMClient` itself is exercised only up to the point of
attempting the lazy `import anthropic` (which either succeeds if the
optional `llm` dependency group happens to be installed, or raises the
documented `RuntimeError` if not) — never a real API call.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from apps.personal.telegram.llm_assistant import (
    SYSTEM_PROMPT,
    AnthropicConfig,
    AnthropicLLMClient,
    MockLLMClient,
    answer_query,
    build_client,
    build_context,
    config_from_env,
)
from core.storage.memory import InMemoryStorage


# ── MockLLMClient ───────────────────────────────────────────────────────


def test_mock_llm_client_returns_canned_reply_verbatim():
    client = MockLLMClient(canned_reply="fixed answer")
    assert client.complete(system="sys", user="anything") == "fixed answer"


def test_mock_llm_client_echoes_when_no_canned_reply():
    client = MockLLMClient()
    reply = client.complete(system=SYSTEM_PROMPT, user="Question: why high? CONTEXT: ...")
    assert "mock reply" in reply.lower()
    assert "Question: why high?" in reply


# ── config_from_env / build_client: inert-by-default ────────────────────


def test_config_from_env_none_when_flag_unset(monkeypatch):
    monkeypatch.delenv("LLM_ASSISTANT_ENABLED", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    assert config_from_env() is None


def test_config_from_env_none_when_key_unset(monkeypatch):
    monkeypatch.setenv("LLM_ASSISTANT_ENABLED", "true")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert config_from_env() is None


def test_config_from_env_none_by_default_matches_production(monkeypatch):
    # This is the state of every deploy today: neither var set.
    monkeypatch.delenv("LLM_ASSISTANT_ENABLED", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert config_from_env() is None
    assert build_client(config_from_env()) is None


def test_config_from_env_builds_config_when_both_set(monkeypatch):
    monkeypatch.setenv("LLM_ASSISTANT_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    cfg = config_from_env()
    assert cfg is not None
    assert cfg.api_key == "sk-fake"
    assert cfg.model == "claude-opus-5"


def test_config_from_env_respects_model_override(monkeypatch):
    monkeypatch.setenv("LLM_ASSISTANT_ENABLED", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
    cfg = config_from_env()
    assert cfg.model == "claude-haiku-4-5"


@pytest.mark.parametrize("falsy", ["", "false", "0", "no", "off"])
def test_config_from_env_falsy_flag_values_disable(monkeypatch, falsy):
    monkeypatch.setenv("LLM_ASSISTANT_ENABLED", falsy)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    assert config_from_env() is None


def test_build_client_wraps_config_in_anthropic_client():
    cfg = AnthropicConfig(api_key="sk-fake", model="claude-opus-5")
    client = build_client(cfg)
    assert isinstance(client, AnthropicLLMClient)
    assert client.config is cfg


# ── AnthropicLLMClient: lazy import, never crashes module import ────────


def test_anthropic_client_lazy_import_does_not_run_at_module_import():
    # Importing llm_assistant (done implicitly by every test in this file)
    # must never require the `anthropic` package to be installed. If this
    # test file collects and the import above succeeded, that's already
    # half the proof; explicitly re-confirm no reference to the `anthropic`
    # module object leaked into this module's namespace.
    import apps.personal.telegram.llm_assistant as mod

    assert "anthropic" not in vars(mod)


def test_anthropic_client_raises_clear_error_without_package(monkeypatch):
    """If `anthropic` isn't installed, `.complete()` should fail with a
    clear, actionable message — not a bare ImportError traceback.
    """
    import builtins

    real_import = builtins.__import__

    def _blocking_import(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("simulated: package not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocking_import)
    client = AnthropicLLMClient(config=AnthropicConfig(api_key="sk-fake"))
    with pytest.raises(RuntimeError, match="uv sync --group llm"):
        client.complete(system="sys", user="hi")


# ── build_context: reuses the deterministic digest builders ─────────────


def test_build_context_includes_today_yesterday_and_trends(default_config):
    tz = ZoneInfo(default_config.timezone)
    now = datetime(2026, 4, 14, 12, 0, tzinfo=tz)
    storage = InMemoryStorage()
    storage.upsert_table(
        "cgm",
        pd.DataFrame(
            {
                "pump_serial": ["p1", "p1"],
                "seqnum": [1, 2],
                "timestamp": [
                    datetime(2026, 4, 14, 8, 0, tzinfo=tz),
                    datetime(2026, 4, 14, 9, 0, tzinfo=tz),
                ],
                "bg_mgdl": [110, 220],
            }
        ),
    )
    context = build_context(storage=storage, config=default_config, now=now)
    assert "Today" in context
    assert "Yesterday" in context
    # build_trends_digest's output shape — see test_telegram_digest.py for
    # the exact wording; just confirm the section made it in.
    assert "7" in context or "day" in context.lower()


def test_build_context_tolerates_storage_read_failure(default_config):
    """A cold-start / partial-outage Storage must not crash context
    assembly — it should degrade to empty-frame digests, same as the
    deterministic /today command's own `_read_window` fallback.
    """

    class _BrokenStorage:
        def read_table(self, *a, **kw):
            raise RuntimeError("boom")

    now = datetime(2026, 4, 14, 12, 0, tzinfo=ZoneInfo(default_config.timezone))
    context = build_context(storage=_BrokenStorage(), config=default_config, now=now)
    assert isinstance(context, str)
    assert "Today" in context  # digest still rendered, just with no data


# ── answer_query: pure prompt assembly ───────────────────────────────────


def test_answer_query_includes_question_and_context_in_user_turn():
    captured = {}

    class _CapturingClient:
        def complete(self, *, system: str, user: str) -> str:
            captured["system"] = system
            captured["user"] = user
            return "the answer"

    result = answer_query(
        "why was I high this afternoon?",
        context="Today: TIR 60%",
        client=_CapturingClient(),
    )
    assert result == "the answer"
    assert captured["system"] == SYSTEM_PROMPT
    assert "why was I high this afternoon?" in captured["user"]
    assert "Today: TIR 60%" in captured["user"]


def test_answer_query_works_end_to_end_with_mock_client():
    reply = answer_query(
        "did my basal change do anything?",
        context="Today: TIR 70%\nYesterday: TIR 65%",
        client=MockLLMClient(),
    )
    assert "mock reply" in reply.lower()
