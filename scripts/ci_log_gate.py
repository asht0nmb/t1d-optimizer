"""Default-deny output gate for public GitHub Actions logs.

Usage::

    python scripts/ci_log_gate.py -- <command> [args...]

The repo is public, so Actions logs are readable by any signed-in GitHub
user. ``ingestion/redaction.py`` (PR #19) scrubs identifiers at the source,
but that only works while every library logs through Python ``logging`` the
way we expect. This wrapper is an independent second layer: it runs the
command with stdout+stderr merged and lets through ONLY

* log lines from our own loggers (``LOGGER_ALLOWLIST``), in one of the two
  formats our entrypoints configure, re-redacted with ``redaction.redact``;
* ``::add-mask::`` workflow commands, byte-for-byte unchanged (the runner
  needs the raw value to mask it, and does not echo add-mask lines);
* for a Python traceback, one ``[gate] ExceptionType: <redacted message>``
  line.

Everything else (third-party loggers, bare prints, warnings text, HTTP dumps,
other ``::workflow commands::``) is dropped and counted; the count is printed
at the end. The command's exit status is propagated (a signal death is
re-raised). If the gate itself fails, it stops emitting, drains and waits for
the child, and exits non-zero (fail closed).

Needs no network and no secrets. Loads ``ingestion/redaction.py`` by file
path (stdlib-only) so it never imports the ingestion package's heavy deps.
"""

from __future__ import annotations

import importlib.util
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import BinaryIO, Callable, TextIO

# Exact logger names, or dotted prefixes (``ingestion`` matches ``ingestion``
# and ``ingestion.client`` but not ``ingestionx``). Derived from what the
# gated entrypoints actually use:
#   scripts/ci_tandem_smoke.py           -> "ci_tandem_smoke"
#   scripts/sync_tandem_to_supabase.py   -> "__main__" (run by file path)
#   apps/personal/cron/detect_meal_rise  -> "meal_rise_cron"
#   library code                         -> ingestion.* / core.* / scripts.* / apps.*
LOGGER_ALLOWLIST: tuple[str, ...] = (
    "__main__",
    "ci_tandem_smoke",
    "meal_rise_cron",
    "ingestion",
    "core",
    "scripts",
    "apps",
)

_LEVELS = r"(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)"
_NAME = r"(?P<name>[A-Za-z_][\w.]*)"
# scripts/ci_tandem_smoke.py: "%(asctime)s %(levelname)s %(name)s — %(message)s"
_SMOKE_LINE = re.compile(
    rf"^\d{{4}}-\d{{2}}-\d{{2}} \d{{2}}:\d{{2}}:\d{{2}},\d{{3}} {_LEVELS} {_NAME} — "
)
# redaction.configure_logging default and detect_meal_rise basicConfig:
# "%(asctime)s %(name)s [%(levelname)s] %(message)s", datefmt "%H:%M:%S"
_SYNC_LINE = re.compile(rf"^\d{{2}}:\d{{2}}:\d{{2}} {_NAME} \[{_LEVELS}\] ")
_LOG_LINE_PATTERNS = (_SMOKE_LINE, _SYNC_LINE)

_ADD_MASK = "::add-mask::"
_TB_START = "Traceback (most recent call last):"
_TB_CHAIN = (
    "During handling of the above exception, another exception occurred:",
    "The above exception was the direct cause of the following exception:",
)
_EXC_LINE = re.compile(r"^(?P<type>[A-Za-z_][\w.]*)(?::\s?(?P<msg>.*))?$")
# ANSI escapes and C0/C1 control chars (keep tab).
_CONTROL = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_MAX_EXC_MSG = 300

# Env values the gate opportunistically scrubs if present. Not required.
_SENSITIVE_ENV = (
    "TCONNECT_EMAIL", "TCONNECT_PASSWORD", "SUPABASE_DB_URL",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DEXCOM_USERNAME", "DEXCOM_PASSWORD",
)

_TEST_CRASH_ENV = "CI_LOG_GATE_TEST_CRASH"  # test hook: forces an internal error


def _load_redaction() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "ingestion" / "redaction.py"
    spec = importlib.util.spec_from_file_location("_ci_log_gate_redaction", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def logger_allowed(name: str) -> bool:
    return any(name == a or name.startswith(a + ".") for a in LOGGER_ALLOWLIST)


def allowlisted_logger(line: str) -> str | None:
    """Return the logger name if ``line`` is one of our log lines, else None."""
    for pattern in _LOG_LINE_PATTERNS:
        m = pattern.match(line)
        if m:
            name = m.group("name")
            return name if logger_allowed(name) else None
    return None


class Gate:
    """Line classifier. Feed lines with ``feed``; call ``finish`` at EOF."""

    def __init__(self, redaction: ModuleType, write: Callable[[str], None]) -> None:
        self._redaction = redaction
        self._write = write
        self.suppressed = 0
        self._in_tb = False
        self._pending_exc: str | None = None  # last exception line, awaiting chain check
        self._crash_after = 1000 if os.environ.get(_TEST_CRASH_ENV) == "1" else None
        self._seen = 0

    def _emit(self, text: str) -> None:
        self._write(text + "\n")

    def _clean(self, text: str) -> str:
        return _CONTROL.sub("", self._redaction.redact(text))

    def _flush_pending_exc(self) -> None:
        line = self._pending_exc
        self._pending_exc = None
        self._in_tb = False
        if line is None:
            return
        m = _EXC_LINE.match(line)
        if m is None:  # pragma: no cover - guarded by caller
            return
        exc_type, msg = m.group("type"), m.group("msg")
        out = f"[gate] {exc_type}"
        if msg:
            msg = self._clean(msg)
            if len(msg) > _MAX_EXC_MSG:
                msg = msg[:_MAX_EXC_MSG] + "…"
            out += f": {msg}"
        self._emit(out)

    def feed(self, line: str) -> None:
        self._seen += 1
        if self._crash_after is not None and self._seen > self._crash_after:
            raise RuntimeError("test-induced gate crash")

        # Workflow commands: add-mask passes byte-for-byte; also teach our own
        # redactor the value, since the child's registered secrets live in
        # another process. Every other ::command:: is dropped (a third-party
        # ::warning:: would become a public annotation; ::stop-commands::
        # would switch off masking).
        if line.startswith(_ADD_MASK):
            value = line[len(_ADD_MASK):]
            if value.strip():
                self._redaction.register_secret(value.strip())
            self._write(line + "\n")
            return

        if self._in_tb:
            if self._pending_exc is not None:
                if line.strip() == "":
                    self.suppressed += 1
                    return
                if line in _TB_CHAIN:
                    self._pending_exc = None  # superseded by the next traceback
                    self.suppressed += 1
                    return
                self._flush_pending_exc()
                # fall through: classify this line normally
            else:
                if line.strip() == "" or line[:1].isspace() or line == _TB_START:
                    self.suppressed += 1
                    return
                if _EXC_LINE.match(line):
                    self._pending_exc = line
                    self.suppressed += 1
                    return
                # Unrecognised terminator: drop the traceback, classify line.
                self._in_tb = False

        if line == _TB_START:
            self._in_tb = True
            self._pending_exc = None
            self.suppressed += 1
            return

        if allowlisted_logger(line) is not None:
            self._emit(self._clean(line))
            return

        self.suppressed += 1

    def finish(self) -> None:
        if self._pending_exc is not None:
            self._flush_pending_exc()
        self._in_tb = False
        self._emit(f"[gate] suppressed {self.suppressed} lines from non-allowlisted sources")


def _register_env_secrets(redaction: ModuleType) -> None:
    from urllib.parse import urlsplit

    for key in _SENSITIVE_ENV:
        value = os.environ.get(key)
        if not value:
            continue
        redaction.register_secret(value)
        if "://" in value:
            try:
                parts = urlsplit(value)
                for piece in (parts.password, parts.username, parts.hostname):
                    redaction.register_secret(piece)
            except ValueError:
                pass


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")


def run(argv: list[str], out: TextIO | None = None) -> int:
    stream = out if out is not None else sys.stdout

    def write(text: str) -> None:
        stream.write(text)
        stream.flush()

    if not argv or argv[0] != "--" or len(argv) < 2:
        write("usage: ci_log_gate.py -- <command> [args...]\n")
        return 2
    cmd = argv[1:]

    try:
        redaction = _load_redaction()
        _register_env_secrets(redaction)
        gate = Gate(redaction, write)
    except Exception as exc:  # noqa: BLE001 - fail closed before launching
        write(f"[gate] internal error: {type(exc).__name__}; command not run\n")
        return 70

    child_env = dict(os.environ)
    child_env.setdefault("PYTHONUNBUFFERED", "1")
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=child_env,
        )
    except OSError as exc:
        write(f"[gate] could not start command: {type(exc).__name__}\n")
        return 127

    def forward(signum, _frame):
        try:
            proc.send_signal(signum)
        except OSError:
            pass

    for sig in (signal.SIGTERM, signal.SIGINT, getattr(signal, "SIGHUP", None)):
        if sig is not None:
            signal.signal(sig, forward)

    assert proc.stdout is not None
    pipe: BinaryIO = proc.stdout
    gate_error: BaseException | None = None
    try:
        for raw in iter(pipe.readline, b""):
            gate.feed(_decode(raw))
        gate.finish()
    except Exception as exc:  # noqa: BLE001 - fail closed
        gate_error = exc
        try:
            for _ in iter(pipe.readline, b""):  # drain so the child never blocks
                pass
        except Exception:  # noqa: BLE001
            pass
    finally:
        rc = proc.wait()

    if gate_error is not None:
        try:
            write(f"[gate] internal error: {type(gate_error).__name__}; output suppressed\n")
        except Exception:  # noqa: BLE001
            pass
        return rc if rc > 0 else 70

    if rc < 0:  # child died from a signal: die the same way
        signum = -rc
        try:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
        except (OSError, ValueError):
            pass
        return 128 + signum
    return rc


def main() -> int:
    try:
        return run(sys.argv[1:])
    except Exception:  # noqa: BLE001 - last-resort fail closed, no detail
        try:
            sys.stdout.write("[gate] internal error; output suppressed\n")
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass
        return 70


if __name__ == "__main__":
    sys.exit(main())
