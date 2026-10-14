"""Owner-run CLI to wire the Telegram command bot to the Vercel worker.

Run from the repo root. Reads ``TELEGRAM_BOT_TOKEN``, ``TELEGRAM_CHAT_ID``
and ``TELEGRAM_WEBHOOK_SECRET`` from ``.env``.

    uv run python scripts/setup_telegram_webhook.py gen-secret
    uv run python scripts/setup_telegram_webhook.py register [--url URL]
    uv run python scripts/setup_telegram_webhook.py verify [--live-test]
    uv run python scripts/setup_telegram_webhook.py delete

Secrets are only ever sent in HTTPS request bodies/headers or the Telegram
Bot API URL path (which is how Telegram authenticates the bot). They are
never printed, logged, or placed on a command line. Exceptions from
``requests`` embed the request URL (which contains the bot token), so
errors are reported by exception class only.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = REPO_ROOT / ".env"
DEFAULT_WORKER_URL = "https://t1d-optimizer-meal-bot.vercel.app/api/telegram"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
TIMEOUT = 15
API = "https://api.telegram.org"


def _load(env_path: Path) -> dict[str, str]:
    return {k: v for k, v in dotenv_values(env_path).items() if v}


def _require(env: dict[str, str], *keys: str) -> bool:
    missing = [k for k in keys if not env.get(k)]
    for k in missing:
        print(f"ERROR: {k} is missing from .env", file=sys.stderr)
    return not missing


def _tg(token: str, method: str, payload: dict | None = None) -> dict | None:
    """Call a Bot API method. Returns parsed JSON or None on transport error."""
    try:
        resp = requests.post(f"{API}/bot{token}/{method}", json=payload or {}, timeout=TIMEOUT)
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        # Never print exc itself: it may contain the token-bearing URL.
        print(f"ERROR: {method} request failed ({type(exc).__name__})", file=sys.stderr)
        return None


def _fake_update(chat_id: str, text: str = "/help") -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "date": 0,
            "chat": {"id": int(chat_id) if chat_id.lstrip("-").isdigit() else chat_id, "type": "private"},
            "text": text,
            "entities": [{"type": "bot_command", "offset": 0, "length": len(text)}],
        },
    }


def cmd_gen_secret(env_path: Path) -> int:
    if _load(env_path).get("TELEGRAM_WEBHOOK_SECRET"):
        print("TELEGRAM_WEBHOOK_SECRET already present in .env; leaving it unchanged.")
        return 0
    existing = env_path.read_text() if env_path.exists() else ""
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    # token_urlsafe emits only A-Z a-z 0-9 _ - ; 48 bytes -> 64 chars (limit 256).
    value = secrets.token_urlsafe(48)
    with env_path.open("a") as fh:
        fh.write(f"{prefix}TELEGRAM_WEBHOOK_SECRET={value}\n")
    print(f"Appended TELEGRAM_WEBHOOK_SECRET to {env_path.name} (value not shown).")
    return 0


def cmd_register(env_path: Path, url: str) -> int:
    env = _load(env_path)
    if not _require(env, "TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET"):
        return 1
    if urlparse(url).scheme != "https":
        print("ERROR: webhook url must be https", file=sys.stderr)
        return 1
    result = _tg(
        env["TELEGRAM_BOT_TOKEN"],
        "setWebhook",
        {
            "url": url,
            "secret_token": env["TELEGRAM_WEBHOOK_SECRET"],
            "allowed_updates": ["message"],
            "drop_pending_updates": True,
        },
    )
    if result is None:
        return 1
    print(f"setWebhook ok={result.get('ok')} description={result.get('description')!r}")
    return 0 if result.get("ok") else 1


def cmd_delete(env_path: Path) -> int:
    env = _load(env_path)
    if not _require(env, "TELEGRAM_BOT_TOKEN"):
        return 1
    result = _tg(env["TELEGRAM_BOT_TOKEN"], "deleteWebhook", {})
    if result is None:
        return 1
    print(f"deleteWebhook ok={result.get('ok')} description={result.get('description')!r}")
    return 0 if result.get("ok") else 1


def _post_worker(url: str, body: dict, secret: str | None) -> requests.Response | None:
    headers = {SECRET_HEADER: secret} if secret else {}
    try:
        return requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as exc:
        print(f"ERROR: worker request failed ({type(exc).__name__})", file=sys.stderr)
        return None


def cmd_verify(env_path: Path, url: str, live_test: bool) -> int:
    env = _load(env_path)
    needed = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"] + (["TELEGRAM_WEBHOOK_SECRET"] if live_test else [])
    if not _require(env, *needed):
        return 1
    ok = True

    info = _tg(env["TELEGRAM_BOT_TOKEN"], "getWebhookInfo")
    if info is None or not info.get("ok"):
        print("getWebhookInfo: FAILED")
        return 1
    r = info.get("result", {})
    host = urlparse(r.get("url") or "").netloc or "(none registered)"
    print(f"webhook host:  {host}")
    print(f"pending count: {r.get('pending_update_count')}")
    print(f"last_error:    {r.get('last_error_message') or 'none'}")
    if not r.get("url"):
        print("  -> no webhook registered; run `register`.")
        ok = False

    resp = _post_worker(url, _fake_update(env["TELEGRAM_CHAT_ID"]), None)
    if resp is not None and resp.status_code == 401:
        print("unauthenticated POST -> 401 (expected): PASS")
    else:
        got = resp.status_code if resp is not None else "no response"
        print(f"unauthenticated POST -> {got} (expected 401): FAIL")
        ok = False

    if live_test:
        resp = _post_worker(url, _fake_update(env["TELEGRAM_CHAT_ID"]), env["TELEGRAM_WEBHOOK_SECRET"])
        replied = False
        if resp is not None and resp.status_code == 200:
            try:
                replied = resp.json().get("replied") is True
            except ValueError:
                pass
        if replied:
            print("signed /help POST -> 200 replied:true: PASS (check Telegram for the help message)")
        else:
            got = resp.status_code if resp is not None else "no response"
            print(f"signed /help POST -> {got}, replied:true not seen: FAIL")
            ok = False
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gen-secret", help="append TELEGRAM_WEBHOOK_SECRET to .env if missing")
    for name in ("register", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--url", default=DEFAULT_WORKER_URL, help="worker /api/telegram URL")
        if name == "verify":
            p.add_argument("--live-test", action="store_true", help="send a signed fake /help update")
    sub.add_parser("delete", help="deleteWebhook (rollback)")
    args = parser.parse_args(argv)

    if args.cmd == "gen-secret":
        return cmd_gen_secret(args.env_file)
    if args.cmd == "register":
        return cmd_register(args.env_file, args.url)
    if args.cmd == "verify":
        return cmd_verify(args.env_file, args.url, args.live_test)
    return cmd_delete(args.env_file)


if __name__ == "__main__":
    raise SystemExit(main())
