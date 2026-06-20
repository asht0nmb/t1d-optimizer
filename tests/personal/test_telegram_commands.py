"""Tests for Telegram update parsing."""

from __future__ import annotations

from apps.personal.telegram.commands import parse_update


def _update(text, chat_id=123):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text}}


def test_parses_known_command():
    p = parse_update(_update("/today"))
    assert p.command == "today"
    assert p.chat_id == "123"
    assert p.is_known is True


def test_strips_botname_suffix_and_args():
    p = parse_update(_update("/trends@MyBot 7"))
    assert p.command == "trends"
    assert p.is_known is True


def test_unknown_command_flagged_not_known():
    p = parse_update(_update("/wat"))
    assert p.command == "wat"
    assert p.is_known is False


def test_non_command_text_has_no_command():
    p = parse_update(_update("hello there"))
    assert p.command is None
    assert p.is_known is False
    assert p.chat_id == "123"  # chat still extracted


def test_edited_message_supported():
    update = {"edited_message": {"chat": {"id": 9}, "text": "/status"}}
    p = parse_update(update)
    assert p.command == "status"
    assert p.chat_id == "9"


def test_update_without_message():
    p = parse_update({"update_id": 5})
    assert p.command is None
    assert p.chat_id is None


def test_case_insensitive():
    assert parse_update(_update("/TODAY")).command == "today"
