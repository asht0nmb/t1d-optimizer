"""Tests for the handler's LLM-assistant routing (research branch, unwired).

Kept in its own file rather than extending `test_telegram_handler.py`, so
the pre-existing (production-relevant) handler tests stay untouched and
this file can be read as "everything new the LLM assistant added to
dispatch." Every existing `process_webhook`/`build_reply` behavior — the
whole prior test file — passes unchanged, since `llm_client` defaults to
`None` and free-text routing only activates when it's explicitly provided.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from apps.personal.telegram.handler import build_reply, process_webhook
from apps.personal.telegram.commands import parse_update
from apps.personal.telegram.llm_assistant import MockLLMClient
from core.storage.memory import InMemoryStorage


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "topsecret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "555")


def _headers(secret="topsecret"):
    return {"X-Telegram-Bot-Api-Secret-Token": secret}


def _update(text, chat_id=555):
    return {"message": {"chat": {"id": chat_id}, "text": text}}


def _now(default_config):
    return datetime(2026, 4, 14, 12, 0, tzinfo=ZoneInfo(default_config.timezone))


# ── build_reply: unit-level routing ──────────────────────────────────────


def test_free_text_without_llm_client_falls_back_to_help(default_config):
    parsed = parse_update(_update("why was I high today?"))
    reply = build_reply(
        parsed, storage=InMemoryStorage(), config=default_config, now=_now(default_config)
    )
    assert "/today" in reply  # help text, same as today's behavior


def test_free_text_with_llm_client_routes_to_assistant(default_config):
    parsed = parse_update(_update("why was I high today?"))
    reply = build_reply(
        parsed,
        storage=InMemoryStorage(),
        config=default_config,
        now=_now(default_config),
        llm_client=MockLLMClient(canned_reply="you ran high after lunch"),
    )
    assert reply == "you ran high after lunch"


def test_unknown_slash_command_never_routes_to_llm(default_config):
    """A typo'd /command (e.g. /todya) is still help, not a question — even
    with an LLM client configured. Only genuinely non-slash text routes.
    """
    parsed = parse_update(_update("/todya"))
    reply = build_reply(
        parsed,
        storage=InMemoryStorage(),
        config=default_config,
        now=_now(default_config),
        llm_client=MockLLMClient(canned_reply="should not see this"),
    )
    assert "/today" in reply
    assert reply != "should not see this"


def test_known_commands_unaffected_by_llm_client_presence(default_config):
    """/today etc. keep dispatching deterministically even when an LLM
    client is configured — the assistant only ever sees the fallthrough.
    """
    parsed = parse_update(_update("/status"))
    reply = build_reply(
        parsed,
        storage=InMemoryStorage(),
        config=default_config,
        now=_now(default_config),
        llm_client=MockLLMClient(canned_reply="should not see this"),
    )
    assert reply != "should not see this"


def test_empty_text_falls_back_to_help_even_with_llm_client(default_config):
    parsed = parse_update(_update(""))
    reply = build_reply(
        parsed,
        storage=InMemoryStorage(),
        config=default_config,
        now=_now(default_config),
        llm_client=MockLLMClient(canned_reply="should not see this"),
    )
    assert reply != "should not see this"


def test_llm_path_exception_falls_back_to_help_not_a_crash(default_config, caplog):
    class _ExplodingClient:
        def complete(self, *, system, user):
            raise RuntimeError("simulated API failure")

    parsed = parse_update(_update("why was I high today?"))
    reply = build_reply(
        parsed,
        storage=InMemoryStorage(),
        config=default_config,
        now=_now(default_config),
        llm_client=_ExplodingClient(),
    )
    assert "/today" in reply  # degraded to help text, not an exception


# ── process_webhook: full path, llm_client threaded through ─────────────


def test_process_webhook_defaults_to_no_llm_client(default_config):
    """The exact call shape api/telegram.py used before this feature
    existed (no llm_client kwarg) must still behave identically.
    """
    sent = []
    status, body = process_webhook(
        body=_update("why was I high today?"),
        headers=_headers(),
        storage_factory=InMemoryStorage,
        config=default_config,
        send=lambda c, t: sent.append((c, t)) or True,
        now=_now(default_config),
    )
    assert status == 200 and body["replied"] is True
    assert "/today" in sent[0][1]


def test_process_webhook_routes_free_text_when_llm_client_passed(default_config):
    sent = []
    status, body = process_webhook(
        body=_update("why was I high today?"),
        headers=_headers(),
        storage_factory=InMemoryStorage,
        config=default_config,
        send=lambda c, t: sent.append((c, t)) or True,
        now=_now(default_config),
        llm_client=MockLLMClient(canned_reply="you ran high after lunch"),
    )
    assert status == 200 and body["replied"] is True
    assert sent[0][1] == "you ran high after lunch"
