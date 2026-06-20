"""Parse incoming Telegram webhook updates into a normalized command.

Pure: no I/O, no network. The handler verifies the request and dispatches
on the returned :class:`ParsedCommand`.
"""

from __future__ import annotations

from dataclasses import dataclass

# The deterministic command set. Anything else (including no leading slash)
# falls through to the help reply — we never echo unknown input back.
KNOWN_COMMANDS = frozenset({"today", "yesterday", "trends", "status", "help"})


@dataclass(frozen=True)
class ParsedCommand:
    """One parsed update.

    Attributes:
        command: Normalized command name (no slash, no ``@botname`` suffix,
            lowercased) when the text is a recognized command; ``None``
            when the update carries no usable command token.
        chat_id: The chat the update came from, as a string, or ``None``
            when the update has no message/chat (e.g. a non-message update).
        is_known: ``True`` when ``command`` is in :data:`KNOWN_COMMANDS`.
    """

    command: str | None
    chat_id: str | None
    is_known: bool


def _extract_message(update: dict) -> dict | None:
    if not isinstance(update, dict):
        return None
    for key in ("message", "edited_message", "channel_post"):
        msg = update.get(key)
        if isinstance(msg, dict):
            return msg
    return None


def _normalize_command(text: str) -> str | None:
    """Return the normalized command token from message text, or ``None``."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    token = stripped.split()[0][1:]  # drop leading slash, take first word
    token = token.split("@", 1)[0]  # drop @botname mention suffix
    return token.lower() or None


def parse_update(update: dict) -> ParsedCommand:
    """Normalize a Telegram update into a :class:`ParsedCommand`."""
    message = _extract_message(update)
    if message is None:
        return ParsedCommand(command=None, chat_id=None, is_known=False)

    chat = message.get("chat")
    chat_id = None
    if isinstance(chat, dict) and chat.get("id") is not None:
        chat_id = str(chat["id"])

    text = message.get("text")
    command = _normalize_command(text) if isinstance(text, str) else None
    is_known = command in KNOWN_COMMANDS
    return ParsedCommand(command=command, chat_id=chat_id, is_known=is_known)
