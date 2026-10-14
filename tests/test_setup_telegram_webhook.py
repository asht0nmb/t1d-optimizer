"""Tests for scripts/setup_telegram_webhook.py (HTTP mocked; no network)."""

from __future__ import annotations

import json
import re

import pytest
import requests

from scripts import setup_telegram_webhook as stw

TOKEN = "123:FAKE_TOKEN_abc"
SECRET = "fake-secret_XYZ"
WORKER = "https://worker.example/api/telegram"
TG = f"https://api.telegram.org/bot{TOKEN}"


@pytest.fixture
def env_file(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        f"TELEGRAM_BOT_TOKEN={TOKEN}\nTELEGRAM_CHAT_ID=42\nTELEGRAM_WEBHOOK_SECRET={SECRET}\n"
    )
    return p


def test_gen_secret_appends_valid_secret_without_printing(tmp_path, capsys):
    p = tmp_path / ".env"
    p.write_text("TELEGRAM_BOT_TOKEN=x")  # no trailing newline
    assert stw.main(["--env-file", str(p), "gen-secret"]) == 0
    value = stw._load(p)["TELEGRAM_WEBHOOK_SECRET"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value)
    assert stw._load(p)["TELEGRAM_BOT_TOKEN"] == "x"
    out = capsys.readouterr()
    assert value not in out.out + out.err


def test_gen_secret_idempotent(env_file):
    before = env_file.read_text()
    assert stw.main(["--env-file", str(env_file), "gen-secret"]) == 0
    assert env_file.read_text() == before


def test_register_payload(env_file, requests_mock, capsys):
    m = requests_mock.post(f"{TG}/setWebhook", json={"ok": True, "description": "Webhook was set"})
    assert stw.main(["--env-file", str(env_file), "register", "--url", WORKER]) == 0
    assert m.last_request.json() == {
        "url": WORKER,
        "secret_token": SECRET,
        "allowed_updates": ["message"],
        "drop_pending_updates": True,
    }
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err and TOKEN not in out.out + out.err


def test_register_requires_secret(tmp_path, requests_mock):
    p = tmp_path / ".env"
    p.write_text(f"TELEGRAM_BOT_TOKEN={TOKEN}\n")
    assert stw.main(["--env-file", str(p), "register"]) == 1
    assert requests_mock.call_count == 0


def test_register_rejects_http(env_file, requests_mock):
    assert stw.main(["--env-file", str(env_file), "register", "--url", "http://x/api/telegram"]) == 1
    assert requests_mock.call_count == 0


def test_register_transport_error_does_not_leak_token(env_file, requests_mock, capsys):
    requests_mock.post(f"{TG}/setWebhook", exc=requests.ConnectionError(f"boom {TOKEN}"))
    assert stw.main(["--env-file", str(env_file), "register"]) == 1
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err


def test_delete(env_file, requests_mock):
    m = requests_mock.post(f"{TG}/deleteWebhook", json={"ok": True})
    assert stw.main(["--env-file", str(env_file), "delete"]) == 0
    assert m.called


def _info(url=WORKER, err=None):
    r = {"url": url, "pending_update_count": 3}
    if err:
        r["last_error_message"] = err
    return {"ok": True, "result": r}


def test_verify_pass_and_no_secret_leak(env_file, requests_mock, capsys):
    requests_mock.post(f"{TG}/getWebhookInfo", json=_info(err="Wrong response 401"))
    w = requests_mock.post(WORKER, status_code=401, json={"error": "unauthorized"})
    assert stw.main(["--env-file", str(env_file), "verify", "--url", WORKER]) == 0
    assert stw.SECRET_HEADER not in w.last_request.headers
    out = capsys.readouterr().out
    assert "worker.example" in out and "pending count: 3" in out and "Wrong response 401" in out
    assert TOKEN not in out and SECRET not in out
    assert "/api/telegram" not in out  # only the host is shown


def test_verify_fails_when_unauth_not_401(env_file, requests_mock):
    requests_mock.post(f"{TG}/getWebhookInfo", json=_info())
    requests_mock.post(WORKER, status_code=200, json={"ok": True})
    assert stw.main(["--env-file", str(env_file), "verify", "--url", WORKER]) == 1


def test_verify_fails_when_no_webhook(env_file, requests_mock):
    requests_mock.post(f"{TG}/getWebhookInfo", json=_info(url=""))
    requests_mock.post(WORKER, status_code=401, json={})
    assert stw.main(["--env-file", str(env_file), "verify", "--url", WORKER]) == 1


def test_verify_live_test(env_file, requests_mock, capsys):
    requests_mock.post(f"{TG}/getWebhookInfo", json=_info())

    def respond(request, context):
        if request.headers.get(stw.SECRET_HEADER) == SECRET:
            context.status_code = 200
            return {"ok": True, "replied": True, "command": "help"}
        context.status_code = 401
        return {"error": "unauthorized"}

    w = requests_mock.post(WORKER, json=respond)
    assert stw.main(["--env-file", str(env_file), "verify", "--url", WORKER, "--live-test"]) == 0
    body = json.loads(w.request_history[-1].body)
    assert body["message"]["chat"]["id"] == 42 and body["message"]["text"] == "/help"
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err


def test_verify_live_test_fails_without_reply(env_file, requests_mock):
    requests_mock.post(f"{TG}/getWebhookInfo", json=_info())
    requests_mock.post(WORKER, [
        {"status_code": 401, "json": {}},
        {"status_code": 200, "json": {"ok": True, "replied": False}},
    ])
    assert stw.main(["--env-file", str(env_file), "verify", "--url", WORKER, "--live-test"]) == 1


@pytest.mark.parametrize("initial", ["TELEGRAM_WEBHOOK_SECRET=\n", "TELEGRAM_WEBHOOK_SECRET=''\n", "export TELEGRAM_WEBHOOK_SECRET=\n"])
def test_gen_secret_replaces_empty_key_in_place(tmp_path, initial):
    p = tmp_path / ".env"
    p.write_text("A=1\n" + initial + "B=2\n")
    assert stw.main(["--env-file", str(p), "gen-secret"]) == 0
    text = p.read_text()
    assert text.count("TELEGRAM_WEBHOOK_SECRET") == 1
    assert stw._load(p)["TELEGRAM_WEBHOOK_SECRET"] and stw._load(p)["A"] == "1" and stw._load(p)["B"] == "2"


def test_gen_secret_new_file_is_0600(tmp_path):
    p = tmp_path / ".env"
    assert stw.main(["--env-file", str(p), "gen-secret"]) == 0
    assert (p.stat().st_mode & 0o777) == 0o600


def test_print_env_raw_value_handles_quotes_and_export(tmp_path, capsys):
    p = tmp_path / ".env"
    p.write_text("export TELEGRAM_BOT_TOKEN='1:abc'\n")
    assert stw.main(["--env-file", str(p), "print-env", "TELEGRAM_BOT_TOKEN"]) == 0
    assert capsys.readouterr().out == "1:abc"


def test_print_env_missing_and_disallowed(tmp_path):
    p = tmp_path / ".env"
    p.write_text("X=1\n")
    assert stw.main(["--env-file", str(p), "print-env", "TELEGRAM_CHAT_ID"]) == 1
    with pytest.raises(SystemExit):
        stw.main(["--env-file", str(p), "print-env", "SUPABASE_DB_URL"])
