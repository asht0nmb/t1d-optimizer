"""PII-safe logging for the public GitHub Actions logs.

The repo is public, so Actions logs are public. Logs must never carry pump
serials, pumper / device-assignment UUIDs, account emails, or the Telegram
bot token. tconnectsync logs its own request URLs (``.../pump-logs/<uuid>?
pumperId=<uuid>``) at WARNING, and exception text may echo response bodies,
so redaction is applied at the *handler* level (formatter + filter) rather
than at each call site.

Layers:
    1. ``RedactingFormatter`` redacts the final formatted line, which covers
       the message, interpolated record args, and traceback text.
    2. ``RedactingFilter`` rewrites ``msg`` / ``args`` / ``exc_text`` on the
       record before any handler formats it.
    3. ``install_redaction`` attaches both to every handler on every logger.
    4. ``redacting_excepthook`` covers uncaught exceptions, which bypass
       logging entirely.
    5. ``emit_github_masks`` (Actions only) asks the runner to mask the real
       identifier values as well. Belt-and-braces; never emitted locally.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import traceback
from typing import Iterable, TextIO

__all__ = [
    "RedactingFilter",
    "RedactingFormatter",
    "clear_secrets",
    "configure_logging",
    "emit_github_masks",
    "install_redaction",
    "mask_serial",
    "protect_identifiers",
    "quiet_tconnectsync",
    "redact",
    "redacting_excepthook",
    "register_name",
    "register_secret",
    "register_serial",
]

_MIN_SECRET_LEN = 5  # shorter values would shred unrelated text

_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
# Linear-time email matching. The naive ``[local]+@domain`` is O(n^2) on a long
# run of local-part characters with no ``@``: the engine restarts at every
# position in the run and each attempt rescans to the run's end (~2.3 s per
# 64 KB). Instead, consume the whole local-part run once (possessive) and make
# the ``@domain`` tail optional; ``_redact_emails`` keeps the run unchanged when
# the tail is absent. This is exactly equivalent to the old pattern: the
# leftmost possible match start in a run is always the run start, and if that
# start fails every later start in the run fails the same way. Domain labels
# cannot contain ``.``, so possessive labels never give up a needed character.
# No length bounds: a bound would fail *open* (leave part of an over-long
# address visible), and this layer must fail closed.
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]++(@[A-Za-z0-9\-]++(?:\.[A-Za-z0-9\-]++)++)?"
)
_PUMPER_PARAM_RE = re.compile(r"(?i)(pumperId=)[^&\s'\"]+")
_PUMP_LOGS_RE = re.compile(r"(pump-logs/)[^/?\s'\"]+")
_BOT_TOKEN_RE = re.compile(r"bot\d{6,}+:[A-Za-z0-9_\-]{20,}+")

# tconnectsync logs ``Decoded JWT: {json}`` at INFO on every login. The claims
# hold the patient's name, email and account ids. Redact the whole payload.
_DECODED_JWT_RE = re.compile(r"(Decoded JWT:)[^\n]*")
# Same consume-then-decide shape as ``_EMAIL_RE``: ``eyJeyJeyJ...`` with no dots
# was O(n^2). The header run is taken possessively and the ``.payload.sig``
# tail is optional; ``_redact_jwts`` leaves header-only matches unchanged. A
# later ``eyJ`` in the same header run would need the same tail, so skipping it
# is exact. No length bounds: real JWT payloads are routinely >1 KB.
_JWT_TOKEN_RE = re.compile(
    r"eyJ[A-Za-z0-9_\-]++(\.[A-Za-z0-9_\-]++\.[A-Za-z0-9_\-]*+)?"
)
_CLAIM_KEYS = (
    "given_name", "family_name", "name", "email", "preferred_username",
    "sub", "sid", "accountId", "pumperId",
)
_CLAIM_RE = re.compile(
    r'(["\'](?:' + "|".join(_CLAIM_KEYS) + r')["\']\s*+:\s*+)(["\'])[^"\']*+\2'
)

_MIN_NAME_LEN = 3  # names are matched as whole words, case-insensitively

_ID_TOKEN = "<id>"
_EMAIL_TOKEN = "<email>"
_SECRET_TOKEN = "<redacted>"
_NAME_TOKEN = "<name>"

# value -> replacement text, for identifiers only known at runtime.
_secrets: dict[str, str] = {}
# person-name strings (first, last, full, ...) known only at runtime.
_names: set[str] = set()
_name_re: re.Pattern[str] | None = None


def mask_serial(serial: object) -> str:
    """Return the log-safe form of a pump serial: ``…`` + last 4 chars."""
    s = str(serial)
    return f"…{s[-4:]}" if len(s) >= 4 else "…"


def register_secret(value: object, replacement: str | None = None) -> None:
    """Register a runtime value to scrub from all subsequent log output."""
    if value is None:
        return
    s = str(value)
    if len(s) < _MIN_SECRET_LEN:
        return
    _secrets[s] = replacement if replacement is not None else _SECRET_TOKEN


def register_serial(serial: object) -> None:
    """Register a pump serial; it is replaced by its masked (last-4) form."""
    if serial is None:
        return
    register_secret(serial, replacement=mask_serial(serial))


def register_name(*parts: object) -> None:
    """Register person-name values (first, last, ...) to scrub from logs.

    Each part, and the space / comma joined combinations of the parts, are
    matched as whole words, case-insensitively. Never hardcode a name: callers
    pass values read from the Tandem account at runtime.
    """
    global _name_re
    clean = [p.strip() for p in parts if isinstance(p, str) and p.strip()]
    variants: set[str] = set(clean)
    if len(clean) >= 2:
        variants.add(" ".join(clean))
        variants.add(" ".join(reversed(clean)))
        variants.add(", ".join(reversed(clean)))
    added = False
    for v in variants:
        if len(v) >= _MIN_NAME_LEN and v not in _names:
            _names.add(v)
            added = True
    if added:
        _name_re = None


def _name_pattern() -> re.Pattern[str] | None:
    global _name_re
    if _name_re is None and _names:
        alts = [
            r"\s+".join(re.escape(tok) for tok in n.split())
            for n in sorted(_names, key=len, reverse=True)
        ]
        _name_re = re.compile(r"(?<!\w)(?:" + "|".join(alts) + r")(?!\w)", re.IGNORECASE)
    return _name_re


def _redact_emails(text: str) -> str:
    return _EMAIL_RE.sub(lambda m: _EMAIL_TOKEN if m.group(1) else m.group(0), text)


def _redact_jwts(text: str) -> str:
    return _JWT_TOKEN_RE.sub(lambda m: "<jwt>" if m.group(1) else m.group(0), text)


def clear_secrets() -> None:
    global _name_re
    _secrets.clear()
    _names.clear()
    _name_re = None


def redact(text: str) -> str:
    """Scrub known secrets, then pattern-matched identifiers, from ``text``."""
    if not text:
        return text
    text = _DECODED_JWT_RE.sub(r"\1 " + _SECRET_TOKEN, text)
    text = _redact_jwts(text)
    text = _CLAIM_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_SECRET_TOKEN}{m.group(2)}", text)
    for value in sorted(_secrets, key=len, reverse=True):
        if value in text:
            text = text.replace(value, _secrets[value])
    name_re = _name_pattern()
    if name_re is not None:
        text = name_re.sub(_NAME_TOKEN, text)
    text = _PUMPER_PARAM_RE.sub(r"\1" + _ID_TOKEN, text)
    text = _PUMP_LOGS_RE.sub(r"\1" + _ID_TOKEN, text)
    text = _BOT_TOKEN_RE.sub("bot" + _SECRET_TOKEN, text)
    text = _UUID_RE.sub(_ID_TOKEN, text)
    text = _redact_emails(text)
    return text


class RedactingFormatter(logging.Formatter):
    """Formatter whose output (message + args + traceback) is redacted."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))

    def formatException(self, ei) -> str:  # noqa: N802 - stdlib name
        return redact(super().formatException(ei))


def _scrub_record(record: logging.LogRecord) -> None:
    """Redact a record's message, args, traceback text and stack info in place."""
    try:
        record.msg = redact(record.getMessage())
        record.args = None
    except Exception:  # noqa: BLE001 - never let logging raise
        record.msg = _SECRET_TOKEN
        record.args = None
    if record.exc_info and not record.exc_text:
        try:
            record.exc_text = redact(
                "".join(traceback.format_exception(*record.exc_info)).rstrip("\n")
            )
        except Exception:  # noqa: BLE001
            record.exc_text = _SECRET_TOKEN
    elif record.exc_text:
        record.exc_text = redact(record.exc_text)
    if record.stack_info:
        record.stack_info = redact(record.stack_info)


class RedactingFilter(logging.Filter):
    """Rewrite a record's message, args and cached traceback text in place."""

    def filter(self, record: logging.LogRecord) -> bool:
        _scrub_record(record)
        return True


def _install_record_factory() -> None:
    """Scrub every record at creation, whatever logger/handler later emits it.

    This is the catch-all for loggers created after ``install_redaction`` with
    ``propagate=False`` and their own (unwrapped) handler: the record is
    already redacted before any handler sees it. Idempotent.
    """
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_redacting", False):
        return

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        _scrub_record(record)
        return record

    factory._redacting = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)


def _harden_handler(handler: logging.Handler) -> None:
    old = handler.formatter
    if not isinstance(old, RedactingFormatter):
        fmt = getattr(old, "_fmt", None) or "%(message)s"
        datefmt = getattr(old, "datefmt", None)
        handler.setFormatter(RedactingFormatter(fmt, datefmt))
    if not any(isinstance(f, RedactingFilter) for f in handler.filters):
        handler.addFilter(RedactingFilter())


def install_redaction() -> None:
    """Harden every handler on the root logger and every named logger.

    Idempotent. Call again after anything that may add handlers (after login
    and after the metadata fetch). Also installs a record factory so a logger
    created later with its own handler is still redacted, and routes
    ``warnings.warn`` output through the (redacting) logging handlers.
    """
    _install_record_factory()
    logging.captureWarnings(True)
    loggers: list[logging.Logger] = [logging.getLogger()]
    loggers += [
        lg
        for lg in logging.Logger.manager.loggerDict.values()
        if isinstance(lg, logging.Logger)
    ]
    for lg in loggers:
        for handler in lg.handlers:
            _harden_handler(handler)


def configure_logging(
    *,
    stream: TextIO | None = None,
    fmt: str = "%(asctime)s %(name)s [%(levelname)s] %(message)s",
    datefmt: str = "%H:%M:%S",
) -> None:
    """Configure root logging at INFO with redaction, and hook uncaught errors.

    Deliberately INFO, never DEBUG: tconnectsync logs the device id at DEBUG.
    """
    root = logging.getLogger()
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(RedactingFormatter(fmt, datefmt))
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    quiet_tconnectsync()
    install_redaction()
    sys.excepthook = redacting_excepthook


def quiet_tconnectsync() -> None:
    """Keep tconnectsync (and children) at WARNING or higher.

    PRIMARY control for the login-time ``Decoded JWT: {...}`` INFO line,
    which carries the patient's name, email and account ids and is emitted
    before any value-based redaction could know what to scrub. Value /
    pattern redaction is only the second layer.
    """
    lg = logging.getLogger("tconnectsync")
    if lg.level == logging.NOTSET or lg.level < logging.WARNING:
        lg.setLevel(logging.WARNING)


def redacting_excepthook(exc_type, exc, tb) -> None:
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    sys.stderr.write(redact(text))


def emit_github_masks(values: Iterable[object], *, out: TextIO | None = None) -> int:
    """Print ``::add-mask::<value>`` for each value, only inside GitHub Actions.

    Written straight to stdout (not via logging) so the redactor cannot mangle
    the value before the runner sees it. Returns the number emitted.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return 0
    stream = out if out is not None else sys.stdout
    seen: set[str] = set()
    count = 0
    for value in values:
        if value is None:
            continue
        s = str(value).strip()
        # A newline would let a value smuggle in a second workflow command.
        if not s or "\n" in s or "\r" in s or s in seen:
            continue
        seen.add(s)
        print(f"::add-mask::{s}", file=stream, flush=True)
        count += 1
    return count


def protect_identifiers(
    *,
    api: object | None = None,
    metadata: Iterable[dict] | None = None,
    pumper: dict | None = None,
    out: TextIO | None = None,
) -> None:
    """Register account identifiers for scrubbing and mask them in Actions.

    Call right after login (``api``: reads the JWT claims tconnectsync keeps
    on ``api.jwtData``), after ``get_pumper()`` (``pumper``: first / last
    name), and after pump metadata is fetched (``metadata``). All idempotent.
    """
    to_mask: list[object] = []

    def _id(value: object) -> None:
        if isinstance(value, str) and value:
            register_secret(value, replacement=_ID_TOKEN)
            to_mask.append(value)

    def _person(first: object, last: object, full: object) -> None:
        register_name(first, last)
        register_name(full)
        for v in (first, last, full, f"{first} {last}" if first and last else None):
            if isinstance(v, str) and len(v.strip()) >= _MIN_NAME_LEN:
                to_mask.append(v.strip())

    if api is not None:
        _id(getattr(api, "pumperId", None))
        _id(getattr(api, "accountId", None))
        claims = getattr(api, "jwtData", None)
        if isinstance(claims, dict):
            _id(claims.get("sub"))
            _id(claims.get("sid"))
            _id(claims.get("accountId"))
            _id(claims.get("pumperId"))
            _person(claims.get("given_name"), claims.get("family_name"), claims.get("name"))
            for key in ("email", "preferred_username"):
                v = claims.get(key)
                if isinstance(v, str) and "@" in v:
                    register_secret(v, replacement=_EMAIL_TOKEN)
    if pumper:
        _person(pumper.get("firstName"), pumper.get("lastName"), pumper.get("name"))
    for pump in metadata or []:
        serial = pump.get("serialNumber")
        if serial:
            register_serial(serial)
            to_mask.append(serial)
        _id(pump.get("assignmentId"))
        _id(pump.get("tconnectDeviceId"))
    email = os.environ.get("TCONNECT_EMAIL")
    if email:
        register_secret(email, replacement=_EMAIL_TOKEN)
    emit_github_masks(to_mask, out=out)
