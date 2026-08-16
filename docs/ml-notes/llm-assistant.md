# LLM Telegram cause-attribution assistant — design notes and manual testing

**Status:** exploratory, branch `research/ml-clustering-and-models`, not merged, **not deployed, no live API key configured anywhere**. Code-complete and locally testable via a mock client; this doc is both the design write-up and the manual testing procedure. Code: `apps/personal/telegram/llm_assistant.py` (new module), plus small additive changes to `apps/personal/telegram/commands.py`, `apps/personal/telegram/handler.py`, and `api/telegram.py` (all backward-compatible — see "How this stays inert" below).

## The gap this fills

`apps/personal/telegram/` today is entirely deterministic: `/today`, `/yesterday`, `/trends`, `/status` each map to one fixed aggregate computed from `Storage`. That's fast, cheap, fully unit-tested, and can only ever answer a question someone thought to hard-code a command for. "Why was I high this afternoon?" or "did switching my basal rate do anything?" have no `/command` to type — they're open-ended questions over the same underlying data, which is the shape of problem an LLM is suited to: given the *right* grounding context, turn a free-text question into a readable, specific answer.

## The one design decision that matters most: the LLM never sees raw data

`build_context()` in `llm_assistant.py` doesn't hand the model a CGM DataFrame or a `Storage` handle. It calls the **existing** digest builders — `apps.personal.telegram.digest.build_day_digest` / `build_trends_digest`, the same functions `/today` and `/trends` already call — and hands the model the *finished text* those produce. Two reasons this matters, not just for security:

1. **Cause attribution needs facts, not vibes.** Give a model a raw glucose trace and ask "why was I high?" and it will confabulate a plausible-sounding story with no way to check it. Give it pre-computed, deterministic aggregates — the exact numbers a human already trusts because they're the same numbers `/today` already showed them — and it can only reason over facts already agreed to be correct.
2. **No LLM call can leak more than a deterministic reply already would.** The model receives text, never a database handle. It's architecturally incapable of running its own query, however cleverly prompted. This also makes the whole pipeline trivially testable — `answer_query()` is a pure function of (question, context string, client), no network or database needed to test it.

## `LLMClient` Protocol: swap the backend without touching the logic

```python
class LLMClient(Protocol):
    def complete(self, *, system: str, user: str) -> str: ...
```

Two implementations:

- **`MockLLMClient`** — deterministic, no network, doesn't even require the `anthropic` package to be installed. This is what the manual testing procedure below runs against, and it's also a legitimate degraded-mode fallback (if a real key is ever unset in a future deploy, the system could keep answering with a clearly-labeled canned message instead of failing).
- **`AnthropicLLMClient`** — the real backend, one non-agentic `messages.create()` call per question (this is single-turn Q&A, not a multi-step agentic workflow, so the plain Messages API is the right tier — see the `claude-api` skill's "Which Surface Should I Use?" guidance). Model ID comes from `ANTHROPIC_MODEL` (default `claude-opus-5`), not hardcoded, since model IDs are exactly the kind of thing that goes stale — see `shared/models.md` in the `claude-api` skill for how fast that catalog moves.

**The `anthropic` SDK import is lazy** — inside `AnthropicLLMClient.complete()`, never at module scope. This matters concretely: `apps/personal/telegram/handler.py` imports `llm_assistant.py` unconditionally (for the `LLMClient` Protocol and `MockLLMClient`), and `handler.py` is imported by the production webhook `api/telegram.py`, which shares a Vercel deploy bundle with the live meal-rise cron worker `api/index.py`. A prior session's own notes on this exact deploy target flag a "root `requirements.txt` bundle size" trap — that file lists only the cron worker's actual runtime deps, deliberately excluding the full `pyproject.toml`/`uv.lock` dependency tree because it's ~654MB and blows Vercel's 500MB limit. Adding `anthropic` there would be exactly that mistake. Instead: `pyproject.toml` gets a new **optional** `llm` dependency group (`uv sync --group llm`), root `requirements.txt` is untouched, and the import only happens if/when `AnthropicLLMClient.complete()` is actually called.

## How this stays inert until the owner wires a key

Every integration point defaults to doing nothing:

- `config_from_env()` returns `None` unless **both** `LLM_ASSISTANT_ENABLED` is a truthy string (`1`/`true`/`yes`/`on`) **and** `ANTHROPIC_API_KEY` is set. Neither is configured in this repo's `.env.example` or in any deploy target today.
- `api/telegram.py` calls `build_client(config_from_env())` and passes the result to `process_webhook(..., llm_client=...)`. When that's `None` (every deploy today), `process_webhook`/`build_reply` behave **exactly** as they did before this feature existed — free-text input still falls through to `help_text()`.
- `apps/personal/telegram/commands.py`'s `ParsedCommand` grew one new field, `raw_text: str | None = None` — additive, defaulted, so it doesn't break any existing call site that constructs `ParsedCommand` without it.
- `apps/personal/telegram/handler.py`'s `build_reply`/`process_webhook` grew one new optional parameter, `llm_client: LLMClient | None = None`. Routing to the assistant only happens when `parsed.command is None` (no recognized `/command`) **and** the text is genuinely non-slash free text (a typo'd `/todya` still gets `help_text()`, never routed to the LLM — see `handler.py`'s `is_free_text` check) **and** `llm_client` is not `None`.
- The LLM path is wrapped in a broad `try/except` (`handler.py`'s `_llm_reply`) — any failure (network error, bad key, malformed response) logs a warning and falls back to `help_text()` rather than crashing the webhook or leaking an exception into a Telegram reply.

Every existing test in `tests/personal/test_telegram_handler.py` passes unmodified — proof that the addition is genuinely backward-compatible, not just "should be." The new routing behavior has its own test file, `tests/personal/test_telegram_handler_llm.py`.

## Manual testing procedure (no live API key required)

Everything below runs with `MockLLMClient` — no network, no `ANTHROPIC_API_KEY`, no `anthropic` package installation needed.

**1. Run the automated test suite** (fastest check that the wiring is correct):

```bash
uv run pytest tests/personal/test_llm_assistant.py tests/personal/test_telegram_handler_llm.py -v
```

**2. Interactive smoke test** — simulate a Telegram free-text message end to end against an in-memory store, with a mock reply. Verified working via `uv run python -i` (interactive) launched from the repo root — the cwd is on `sys.path` there automatically. If saving this as a standalone script under `scripts/`, add the usual `sys.path.insert(0, ...)` bootstrap every other file in `scripts/` uses (the project isn't installed as a package):

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from apps.personal.telegram.commands import parse_update
from apps.personal.telegram.handler import build_reply
from apps.personal.telegram.llm_assistant import MockLLMClient
from core.storage.memory import InMemoryStorage
from detection.config import get_config

config = get_config()
parsed = parse_update({
    "message": {"chat": {"id": 555}, "text": "why was I high this afternoon?"}
})
now = datetime.now(ZoneInfo(config.timezone))

reply = build_reply(
    parsed,
    storage=InMemoryStorage(),  # swap for a real Storage to see real context assembled
    config=config,
    now=now,
    llm_client=MockLLMClient(),  # echoes back what it received — confirms context assembly is sane
)
print(reply)
```

With `MockLLMClient()` (no `canned_reply`), the printed output echoes the first 200 characters of what would have been sent to a real model — including the assembled context — so you can visually confirm `build_context()` is pulling sensible digest text before ever spending a real API call on it.

**3. Testing against real Supabase data** (still no API key — just real context assembly): swap `InMemoryStorage()` above for a real `SupabaseStorage.from_pooler_url(os.environ["SUPABASE_DB_URL"])` (see `scripts/build_daily_features_dataset.py` for the exact connection pattern) and rerun. The mock client's echo will show real `/today`-equivalent digest text pulled from the owner's actual recent data.

## Going live (for the owner, when ready — not done as part of this exploration)

1. `uv add anthropic --group llm` (or `uv sync --group llm` if the group is already declared, which it is as of this branch).
2. Set `ANTHROPIC_API_KEY` and `LLM_ASSISTANT_ENABLED=true` in the Vercel project's environment variables (same project as `api/telegram.py` / `api/index.py`).
3. Optionally set `ANTHROPIC_MODEL` to override the default (`claude-opus-5`).
4. Redeploy. `api/telegram.py`'s `config_from_env()` will now return a real config, `build_client()` will construct a real `AnthropicLLMClient`, and free-text Telegram messages will route to it.
5. **Recommend testing against a small budget/model first** (e.g. `claude-haiku-4-5`) before committing to the default model, given this is a personal project with no cost-monitoring infrastructure built yet.

## What was deliberately left out of this pass

- **No conversation memory / multi-turn state.** Each question is answered independently — no session or thread tracking across messages. Given the existing Telegram surface is already stateless per-command, this matches the existing architecture; multi-turn would be a real follow-up feature, not a natural extension of what's here.
- **No tool use / agentic loop.** This is intentionally the simplest tier (`claude-api` skill: "single LLM call" — classification/summarization/Q&A) rather than an agent that can query Storage itself. Given the "no raw data" design principle above, that's a deliberate choice, not a shortcut — an agentic version that let the model run its own SQL would reintroduce exactly the trust/leakage problem this design avoids.
- **No cost controls, rate limiting, or per-user quota** beyond what Telegram's own chat-allowlist auth already provides (`TELEGRAM_CHAT_ID` — only the owner's own chat gets a reply at all). Fine for a single-user personal project; would need attention before any multi-user use.
