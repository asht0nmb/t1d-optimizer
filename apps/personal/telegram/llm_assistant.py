"""Conversational cause-attribution assistant over the Telegram command surface.

*** RESEARCH / EXPLORATION MODULE — CODE-COMPLETE, DELIBERATELY UNWIRED. ***
No live Anthropic API key is configured anywhere in this repo or its deploy
targets; nothing here is deployed. Every integration point in this module
and in ``apps/personal/telegram/handler.py`` / ``api/telegram.py`` is
inert-by-default: it only activates when *both* ``LLM_ASSISTANT_ENABLED=true``
*and* ``ANTHROPIC_API_KEY`` are set in the environment, which they are not in
production today. The owner will wire a real key and flip the flag himself
when ready — see ``docs/ml-notes/llm-assistant.md`` for the manual testing
procedure (with the bundled :class:`MockLLMClient`, no network required) and
the checklist for going live.

--------------------------------------------------------------------------
WHY THIS EXISTS
--------------------------------------------------------------------------
Today's Telegram surface (``apps/personal/telegram/handler.py``) is
*deterministic*: `/today`, `/yesterday`, `/trends`, `/status` each map to a
fixed aggregate computed straight from ``Storage``. That's fast, cheap, and
fully testable — and it can only answer questions someone thought to hard-
code a command for. "Why was I high this afternoon?" or "did my basal rate
change do anything?" have no `/command` — they're open-ended questions over
the same underlying data, which is exactly the shape an LLM is good at:
given the *right* deterministic context, turn a free-text question into a
grounded, readable answer.

--------------------------------------------------------------------------
DESIGN: THE LLM NEVER TOUCHES RAW DATA OR THE STORAGE LAYER
--------------------------------------------------------------------------
This is the single most important boundary in this module, and it's a
direct consequence of two things this repo already believes:

1. **Cause attribution needs facts, not vibes.** An LLM given a raw CGM
   trace and asked "why was I high?" will confabulate a plausible-sounding
   story. An LLM given *pre-computed, deterministic aggregates* (today's
   digest, TIR trends, meal/insulin summary — the exact same numbers
   `/today` and `/trends` already produce) can only reason over numbers a
   human already trusts, because they're the same numbers already shown
   elsewhere in the app.
2. **No LLM call should be able to leak more than a Telegram reply already
   would.** `build_context()` calls the *existing* digest builders
   (`apps.personal.telegram.digest.build_day_digest` /
   `build_trends_digest` / `build_status_digest`) — the same text a
   deterministic `/today` reply already sends to this same chat. The LLM
   client (`AnthropicLLMClient` or the interchangeable `MockLLMClient`)
   receives that finished text, never a `Storage` handle, never a
   DataFrame. It cannot query anything beyond what's already been
   summarized for it. This also makes `answer_query()` pure and trivially
   testable without a real API key or a real database.

--------------------------------------------------------------------------
DESIGN: `LLMClient` PROTOCOL — SWAP THE BACKEND, NOT THE LOGIC
--------------------------------------------------------------------------
`answer_query()` takes an `LLMClient`, not a concrete Anthropic type. Two
implementations exist:

* `MockLLMClient` — deterministic, no network, no dependency on the
  `anthropic` package being installed at all. This is what
  `docs/ml-notes/llm-assistant.md`'s manual testing procedure and this
  module's own test suite exercise. It's also a legitimate degraded-mode
  fallback: if `ANTHROPIC_API_KEY` is ever unset in a future deploy, the
  system can keep answering with a clearly-labeled canned response instead
  of crashing.
* `AnthropicLLMClient` — the real backend. Built per the `claude-api` skill
  reference: a single non-agentic `messages.create()` call (this is a
  single-turn Q&A task, not a multi-step agentic workflow — see the skill's
  "Which Surface Should I Use?" guidance), reading the model ID from
  `ANTHROPIC_MODEL` (default `claude-opus-5`, override for a cheaper model
  later) rather than hardcoding it, since model IDs are exactly the kind of
  thing that goes stale.

  **The `anthropic` SDK is imported lazily, inside `AnthropicLLMClient`'s
  own methods — never at module import time.** Two reasons, both load-
  bearing:
  1. `apps/personal/telegram/handler.py` (imported by the production
     webhook `api/telegram.py`, which shares a Vercel deploy bundle with
     the live meal-rise cron worker `api/index.py`) will end up importing
     this module for the `LLMClient` Protocol / `MockLLMClient` /
     `build_context` even before this feature is wired up. If importing
     `llm_assistant.py` eagerly imported `anthropic`, that import would
     need to succeed on every production request — including the ones
     that never touch the LLM path — and `anthropic` is deliberately
     **not** in root `requirements.txt` (see that file's own comment and
     `pyproject.toml`'s new `llm` optional group) specifically to avoid
     bloating the cron worker's Vercel deploy bundle. A prior session's
     notes flag this exact bundle-size trap.
  2. It means this whole module — Protocol, mock, context builder, prompt
     assembly — is usable and testable in an environment that has never
     run `uv sync --group llm`, which is the state of this repo today.

--------------------------------------------------------------------------
WHAT "UNWIRED" MEANS, CONCRETELY
--------------------------------------------------------------------------
* `config_from_env()` returns `None` unless *both* `LLM_ASSISTANT_ENABLED`
  is a truthy string and `ANTHROPIC_API_KEY` is set. `api/telegram.py`
  calls it and passes the result straight through to
  `apps.personal.telegram.handler.process_webhook`'s new (optional,
  defaulted-to-`None`) `llm_client` parameter — when it's `None`, every
  existing code path in `handler.py` behaves exactly as it does today
  (unknown/free-text input still falls through to `help_text()`).
* No code path here writes to Supabase, to `config/user_config.yaml`, or
  to any production detection module. This is a read-context, generate-
  reply flow only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

import pandas as pd

from apps.personal.telegram.digest import build_day_digest, build_trends_digest, compute_tir
from core.storage.protocol import Storage
from detection.config import AppConfig

__all__ = [
    "LLMClient",
    "MockLLMClient",
    "AnthropicLLMClient",
    "AnthropicConfig",
    "config_from_env",
    "build_client",
    "build_context",
    "answer_query",
    "SYSTEM_PROMPT",
]

_DEFAULT_MODEL = "claude-opus-5"

SYSTEM_PROMPT = """\
You are a data-grounded assistant for a Type 1 diabetes tracking system. \
The person you're helping wears a CGM and an insulin pump; you're shown a \
short deterministic summary of their recent glucose and insulin data below \
their question, computed the same way the app's own /today and /trends \
commands compute it — not raw sensor data.

Rules:
- Answer only from the CONTEXT block below. If the context doesn't contain \
enough information to answer, say so plainly rather than guessing or \
inventing a mechanism.
- You are not a medical professional and this is not medical advice. Do not \
recommend insulin dose changes, medication changes, or diagnose anything. \
You may describe patterns you observe in the numbers ("your glucose rose \
sharply around the time of your 12:30pm meal bolus") but not prescribe a \
response to them.
- Be concise. This is a Telegram reply, not a report — a few sentences, not \
a wall of text.
- If asked something the context can't answer (e.g. "how did I sleep" when \
no sleep data is included), say plainly that you don't have that data \
rather than speculating.
"""


class LLMClient(Protocol):
    """Backend-agnostic single-turn completion. See module docstring."""

    def complete(self, *, system: str, user: str) -> str:
        """Return the model's text reply to `user`, guided by `system`."""
        ...


@dataclass(frozen=True)
class MockLLMClient:
    """Deterministic, network-free stand-in for local testing.

    Not a toy — this is what `docs/ml-notes/llm-assistant.md`'s manual
    testing procedure runs against, and it's a legitimate degraded-mode
    fallback if a real backend is ever unavailable. `canned_reply`, when
    set, is returned verbatim (useful for asserting exact behavior in a
    handler test); otherwise `complete()` echoes back a clearly-labeled
    summary of what it received, so a human running the manual test can
    visually confirm the context assembly is sane without needing a real
    model to interpret it.
    """

    canned_reply: str | None = None

    def complete(self, *, system: str, user: str) -> str:
        if self.canned_reply is not None:
            return self.canned_reply
        # Echo shape lets a manual tester eyeball that `user` (question +
        # context) looks right without needing a real model in the loop.
        return (
            "[mock reply — no live LLM configured]\n"
            f"Received {len(user)} chars of question+context. "
            f"First 200 chars:\n{user[:200]}"
        )


@dataclass(frozen=True)
class AnthropicConfig:
    """Everything needed to construct a real `AnthropicLLMClient`."""

    api_key: str
    model: str = _DEFAULT_MODEL
    max_tokens: int = 1024


@dataclass(frozen=True)
class AnthropicLLMClient:
    """Real backend: one non-agentic `messages.create()` call per question.

    See module docstring "DESIGN: `LLMClient` PROTOCOL" for why the SDK
    import is lazy (inside `complete`, not at module scope) rather than at
    the top of this file.
    """

    config: AnthropicConfig

    def complete(self, *, system: str, user: str) -> str:
        try:
            import anthropic  # lazy: see module docstring
        except ImportError as exc:
            raise RuntimeError(
                "AnthropicLLMClient requires the 'anthropic' package. "
                "Install it with `uv sync --group llm` (see pyproject.toml's "
                "optional 'llm' dependency group)."
            ) from exc

        client = anthropic.Anthropic(api_key=self.config.api_key)
        response = client.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if response.stop_reason == "refusal":
            # Safety-classifier decline — surface plainly rather than
            # crashing on an empty/partial content array. See the
            # claude-api skill's refusal-handling guidance: never index
            # response.content unconditionally.
            return (
                "I can't answer that one — it looks like it tripped a "
                "content safety check on my end. Try rephrasing, or ask "
                "something more specific about your glucose/insulin data."
            )
        for block in response.content:
            if block.type == "text":
                return block.text
        return "(no text in model response)"


def config_from_env() -> AnthropicConfig | None:
    """Return an `AnthropicConfig` iff the feature is explicitly enabled AND keyed.

    `None` whenever `LLM_ASSISTANT_ENABLED` isn't a truthy string, or
    `ANTHROPIC_API_KEY` is unset — which is the state of every deploy
    target today. Callers (`api/telegram.py`) treat `None` as "run the
    assistant path with `llm_client=None`," which `handler.py` treats
    identically to today's pre-LLM behavior (fall through to `help_text()`).
    """
    enabled = os.environ.get("LLM_ASSISTANT_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not enabled or not api_key:
        return None
    model = os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL)
    return AnthropicConfig(api_key=api_key, model=model)


def build_client(config: AnthropicConfig | None) -> LLMClient | None:
    """`AnthropicConfig` -> `AnthropicLLMClient`, or `None` when unconfigured."""
    if config is None:
        return None
    return AnthropicLLMClient(config=config)


def build_context(
    *, storage: Storage, config: AppConfig, now: datetime
) -> str:
    """Assemble the deterministic text context the LLM reasons over.

    Reuses the *same* digest builders the deterministic `/today` and
    `/trends` commands use (`apps.personal.telegram.digest`), so the LLM
    never sees anything a plain Telegram reply wouldn't already show this
    chat. See module docstring "DESIGN: THE LLM NEVER TOUCHES RAW DATA."

    Covers: today's digest, yesterday's digest (recent-history context for
    "why was today different from usual"-style questions), and the 7/14/30
    day TIR trend. Read-only against `storage`; never raises on missing
    data — falls back to an empty-frame digest exactly as `handler.py`'s
    `_read_window` does, so a cold-start or partial-outage Storage still
    produces *some* usable context rather than crashing the whole reply.
    """
    tz = ZoneInfo(config.timezone)
    today = now.astimezone(tz).date()
    yesterday = today - timedelta(days=1)

    parts = [
        _day_context("Today", today, storage=storage, config=config, tz=tz),
        _day_context("Yesterday", yesterday, storage=storage, config=config, tz=tz),
        _trends_context(storage=storage, config=config, tz=tz, today=today),
    ]
    return "\n\n".join(p for p in parts if p)


def _day_bounds(day, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    return start, start + timedelta(days=1)


def _read_window(storage: Storage, name: str, since, until) -> pd.DataFrame:
    try:
        return storage.read_table(name, since=since, until=until)
    except Exception:
        return pd.DataFrame()


def _day_context(label: str, day, *, storage: Storage, config: AppConfig, tz: ZoneInfo) -> str:
    since, until = _day_bounds(day, tz)
    cgm = _read_window(storage, "cgm", since, until)
    bolus = _read_window(storage, "bolus", since, until)
    requests = _read_window(storage, "requests", since, until)
    return build_day_digest(
        label=label,
        day=day,
        cgm=cgm,
        bolus=bolus,
        requests=requests,
        alert_count=0,  # alert count isn't cause-attribution-relevant context; omit the extra read
        low=config.bg_targets.low,
        high=config.bg_targets.high,
    )


def _trends_context(*, storage: Storage, config: AppConfig, tz: ZoneInfo, today) -> str:
    tir_by_window: dict[int, float | None] = {}
    for window in (7, 14, 30):
        since = datetime(today.year, today.month, today.day, tzinfo=tz) - timedelta(days=window - 1)
        until = datetime(today.year, today.month, today.day, tzinfo=tz) + timedelta(days=1)
        cgm = _read_window(storage, "cgm", since, until)
        bg = cgm["bg_mgdl"] if "bg_mgdl" in cgm.columns else None
        tir_by_window[window] = (
            compute_tir(bg, low=config.bg_targets.low, high=config.bg_targets.high)
            if bg is not None
            else None
        )
    return build_trends_digest(tir_by_window)


def answer_query(question: str, *, context: str, client: LLMClient) -> str:
    """Compose the user turn (question + context) and call `client`.

    Pure given its inputs — no I/O beyond `client.complete()` itself. This
    is what makes it trivially testable with `MockLLMClient` and a hand-
    built context string, independent of Storage/Supabase/network.
    """
    user_turn = f"Question: {question}\n\nCONTEXT:\n{context}"
    return client.complete(system=SYSTEM_PROMPT, user=user_turn)
