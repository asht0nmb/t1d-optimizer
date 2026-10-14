"""Tests for ingestion/redaction.py — PII-safe logging for the public Actions logs."""

from __future__ import annotations

import io
import logging
import sys

import pytest

from ingestion import redaction

SERIAL = "90012345678"
UUID_A = "0b8d3c1e-52f4-4a77-9a10-3c2f8e6d7b21"
UUID_B = "F47AC10B-58CC-4372-A567-0E02B2C3D479"


@pytest.fixture(autouse=True)
def _clean_registry():
    redaction.clear_secrets()
    yield
    redaction.clear_secrets()


@pytest.fixture
def stream_logger():
    """A fresh root handler (with redaction installed) writing to a StringIO."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(name)s [%(levelname)s] %(message)s"))
    root = logging.getLogger()
    old_handlers, old_level = root.handlers[:], root.level
    root.handlers = [handler]
    root.setLevel(logging.DEBUG)
    redaction.install_redaction()
    try:
        yield stream
    finally:
        root.handlers = old_handlers
        root.setLevel(old_level)


class TestMaskSerial:
    def test_last_four(self):
        assert redaction.mask_serial(SERIAL) == "…5678"

    def test_short(self):
        assert redaction.mask_serial("12") == "…"

    def test_idempotent(self):
        assert redaction.mask_serial(redaction.mask_serial(SERIAL)) == "…5678"


class TestRedactText:
    def test_uuid_both_cases(self):
        out = redaction.redact(f"a {UUID_A} b {UUID_B} c")
        assert UUID_A not in out and UUID_B not in out
        assert out.startswith("a ") and out.endswith(" c")

    def test_email(self):
        out = redaction.redact("login failed for first.last+x@example.co.uk now")
        assert "@" not in out

    def test_pumper_id_query_param_non_uuid(self):
        out = redaction.redact("GET /x?pumperId=abc123&startDate=2026-08-21")
        assert "abc123" not in out
        assert "startDate=2026-08-21" in out

    def test_pump_logs_path_segment_non_uuid(self):
        out = redaction.redact("api/reports/bff/pump-logs/dev42?foo=1")
        assert "dev42" not in out
        assert "pump-logs/" in out

    def test_tconnectsync_warning_line(self):
        line = (
            "Received ApiException in TandemSourceApi with endpoint "
            f"'api/reports/bff/pump-logs/{UUID_A}?pumperId={UUID_B}&startDate=x' "
            "(tries 0): HTTP 400"
        )
        out = redaction.redact(line)
        assert UUID_A not in out and UUID_B not in out
        assert "HTTP 400" in out

    def test_known_serial_masked_to_last_four(self):
        redaction.register_serial(SERIAL)
        out = redaction.redact(f"Pump {SERIAL} chunk failed; again {SERIAL}")
        assert SERIAL not in out
        assert out.count("…5678") == 2

    def test_tiny_secrets_ignored(self):
        redaction.register_secret("ab")
        assert redaction.redact("a b ab cab") == "a b ab cab"

    def test_longest_secret_first(self):
        redaction.register_secret("secretvalue1")
        redaction.register_secret("secretvalue12345")
        out = redaction.redact("x secretvalue12345 y")
        assert "12345" not in out

    def test_telegram_bot_token(self):
        out = redaction.redact(
            "POST https://api.telegram.org/bot123456789:AAE-abcdefghijklmnopqrstuvwxyz0123456/sendMessage"
        )
        assert "AAE-abc" not in out

    def test_clean_text_untouched(self):
        s = "Pump …5678: chunk 1/1 (2026-08-21 → 2026-08-22): 1915 events"
        assert redaction.redact(s) == s


class TestHandlerLevelRedaction:
    def test_formatted_message(self, stream_logger):
        logging.getLogger("t").info(f"id {UUID_A}")
        assert UUID_A not in stream_logger.getvalue()
        assert "id " in stream_logger.getvalue()

    def test_record_args(self, stream_logger):
        redaction.register_serial(SERIAL)
        logging.getLogger("t").warning(
            "pump %s uuid %s email %s", SERIAL, UUID_A, "me@example.com"
        )
        out = stream_logger.getvalue()
        assert SERIAL not in out
        assert UUID_A not in out
        assert "me@example.com" not in out

    def test_exception_traceback_text(self, stream_logger):
        redaction.register_serial(SERIAL)
        log = logging.getLogger("t")
        try:
            raise RuntimeError(f"boom pumperId={UUID_B} serial {SERIAL} u@example.org")
        except RuntimeError:
            log.error("chunk FAILED", exc_info=True)
        out = stream_logger.getvalue()
        assert "Traceback" in out
        assert UUID_B not in out
        assert SERIAL not in out
        assert "u@example.org" not in out

    def test_formatter_alone_redacts_exc_text(self):
        # Even without the filter, the formatter scrubs a preset exc_text.
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "m", None, None)
        record.exc_text = f"Traceback ... {UUID_A}"
        out = redaction.RedactingFormatter("%(message)s").format(record)
        assert UUID_A not in out

    def test_formatter_alone_redacts_args_and_exception(self):
        redaction.register_serial(SERIAL)
        try:
            raise RuntimeError(f"x {SERIAL}")
        except RuntimeError:
            exc_info = sys.exc_info()
        record = logging.LogRecord(
            "t", logging.ERROR, __file__, 1, "pump %s", (SERIAL,), exc_info
        )
        out = redaction.RedactingFormatter("%(message)s").format(record)
        assert SERIAL not in out
        assert "RuntimeError" in out

    def test_third_party_child_logger_covered(self, stream_logger):
        logging.getLogger("tconnectsync.api.tandemsource").warning(
            "Received ApiException with endpoint 'api/reports/bff/pump-logs/%s?pumperId=%s'",
            UUID_A,
            UUID_B,
        )
        out = stream_logger.getvalue()
        assert UUID_A not in out and UUID_B not in out

    def test_handler_on_named_logger_is_covered(self):
        stream = io.StringIO()
        lg = logging.getLogger("tconnectsync.custom_test")
        h = logging.StreamHandler(stream)
        lg.addHandler(h)
        lg.propagate = False
        lg.setLevel(logging.INFO)
        try:
            redaction.install_redaction()
            lg.info("id %s", UUID_A)
        finally:
            lg.removeHandler(h)
        assert UUID_A not in stream.getvalue()


class TestConfigureLogging:
    def _run(self, fn):
        root = logging.getLogger()
        old_handlers, old_level = root.handlers[:], root.level
        old_hook = sys.excepthook
        try:
            return fn(root)
        finally:
            root.handlers = old_handlers
            root.setLevel(old_level)
            sys.excepthook = old_hook

    def test_installs_redacting_handler(self):
        stream = io.StringIO()

        def go(root):
            redaction.configure_logging(stream=stream)
            logging.getLogger("x").info("hello %s", UUID_A)

        self._run(go)
        out = stream.getvalue()
        assert "hello" in out
        assert UUID_A not in out

    def test_never_debug(self):
        def go(root):
            redaction.configure_logging(stream=io.StringIO())
            assert root.level == logging.INFO

        self._run(go)


class TestExcepthook:
    def test_uncaught_exception_redacted(self, monkeypatch):
        err = io.StringIO()
        monkeypatch.setattr(sys, "stderr", err)
        try:
            raise ValueError(f"bad {UUID_A}")
        except ValueError:
            redaction.redacting_excepthook(*sys.exc_info())
        assert UUID_A not in err.getvalue()
        assert "ValueError" in err.getvalue()


class TestGithubMasks:
    def test_emitted_in_actions(self, monkeypatch):
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        out = io.StringIO()
        n = redaction.emit_github_masks([SERIAL, UUID_A, UUID_A, "", None], out=out)
        assert out.getvalue().splitlines() == [
            f"::add-mask::{SERIAL}",
            f"::add-mask::{UUID_A}",
        ]
        assert n == 2

    def test_not_emitted_outside_actions(self, monkeypatch):
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        out = io.StringIO()
        assert redaction.emit_github_masks([SERIAL], out=out) == 0
        assert out.getvalue() == ""

    def test_not_emitted_when_not_exactly_true(self, monkeypatch):
        monkeypatch.setenv("GITHUB_ACTIONS", "false")
        out = io.StringIO()
        assert redaction.emit_github_masks([SERIAL], out=out) == 0

    def test_newline_values_skipped(self, monkeypatch):
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        out = io.StringIO()
        redaction.emit_github_masks(["abc\n::warning::x"], out=out)
        assert out.getvalue() == ""


class _FakeApi:
    pumperId = UUID_B


class TestProtectIdentifiers:
    def test_registers_and_masks_in_actions(self, monkeypatch):
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        out = io.StringIO()
        meta = [
            {"serialNumber": SERIAL, "assignmentId": UUID_A, "tconnectDeviceId": UUID_A}
        ]
        redaction.protect_identifiers(api=_FakeApi(), metadata=meta, out=out)
        masked = out.getvalue().splitlines()
        assert f"::add-mask::{SERIAL}" in masked
        assert f"::add-mask::{UUID_A}" in masked
        assert f"::add-mask::{UUID_B}" in masked
        assert SERIAL not in redaction.redact(f"s={SERIAL}")

    def test_no_output_locally_but_still_registers(self, monkeypatch):
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        out = io.StringIO()
        redaction.protect_identifiers(
            api=_FakeApi(), metadata=[{"serialNumber": SERIAL}], out=out
        )
        assert out.getvalue() == ""
        assert SERIAL not in redaction.redact(SERIAL)


# ---------------------------------------------------------------------------
# Person name + JWT redaction (tconnectsync logs "Decoded JWT: {...}" at INFO)
# ---------------------------------------------------------------------------

FIRST, LAST = "Testfirst", "Testlast"
FULL = f"{FIRST} {LAST}"
ACCOUNT = "5c1d2e3f-0000-4aaa-8bbb-123456789abc"
FAKE_JWT = (
    "eyJhbGciOiJSUzI1NiJ9."
    "eyJnaXZlbl9uYW1lIjoiVGVzdCJ9."
    "c2lnbmF0dXJlLXZhbHVlLWhlcmU"
)
DECODED = (
    '{"given_name": "%s", "family_name": "%s", "name": "%s", "email": "p@example.com", '
    '"sub": "abc-sub-123", "sid": "sid-xyz-789", "accountId": "%s", "pumperId": "%s"}'
    % (FIRST, LAST, FULL, ACCOUNT, UUID_A)
)


class TestJwtRedaction:
    def test_decoded_jwt_line_fully_redacted_without_runtime_knowledge(self):
        # Login happens before any name is known: pattern redaction alone must hold.
        out = redaction.redact("Decoded JWT: " + DECODED)
        for needle in (FIRST, LAST, ACCOUNT, UUID_A, "p@example.com", "abc-sub-123", "sid-xyz-789"):
            assert needle not in out
        assert "Decoded JWT" in out  # label kept, payload gone

    def test_claim_keys_redacted_in_arbitrary_dicts(self):
        out = redaction.redact(
            "claims={'given_name': 'Zed', 'family_name': 'Qux', 'iat': 123, 'sid': 'abcdef'}"
        )
        assert "Zed" not in out and "Qux" not in out and "abcdef" not in out
        assert "'iat': 123" in out

    def test_jwt_strings_redacted(self):
        out = redaction.redact(f"token {FAKE_JWT} and Bearer {FAKE_JWT}.")
        assert "eyJ" not in out

    def test_decoded_jwt_through_log_handler(self, stream_logger):
        logging.getLogger("tconnectsync.api.tandemsource").warning("Decoded JWT: %s" % DECODED)
        out = stream_logger.getvalue()
        assert FIRST not in out and LAST not in out and ACCOUNT not in out


class TestNameRedaction:
    def test_first_last_and_combinations(self):
        redaction.register_name(FIRST, LAST)
        out = redaction.redact(
            f"hello {FIRST}; {LAST}, {FIRST}; {FULL}; {LAST} {FIRST}; {FIRST.upper()}"
        )
        assert FIRST.lower() not in out.lower()
        assert LAST.lower() not in out.lower()

    def test_full_name_with_extra_whitespace(self):
        redaction.register_name(FULL)
        assert FIRST not in redaction.redact(f"{FIRST}   {LAST}")

    def test_whole_word_only(self):
        redaction.register_name("Ann", "Smithson")
        assert redaction.redact("Annual report") == "Annual report"

    def test_short_names_ignored(self):
        redaction.register_name("Al", "Li")
        assert redaction.redact("Al and Li") == "Al and Li"

    def test_non_string_parts_ignored(self):
        redaction.register_name(None, 42, object())
        assert redaction.redact("42 None") == "42 None"

    def test_protect_identifiers_from_jwt_claims(self, monkeypatch):
        monkeypatch.setenv("GITHUB_ACTIONS", "true")

        class Api:
            pumperId = UUID_B
            accountId = ACCOUNT
            jwtData = {
                "given_name": FIRST,
                "family_name": LAST,
                "name": FULL,
                "email": "p@example.com",
                "sub": "abc-sub-123",
                "sid": "sid-xyz-789",
            }

        out = io.StringIO()
        redaction.protect_identifiers(api=Api(), out=out)
        masked = out.getvalue().splitlines()
        for v in (FIRST, LAST, FULL, ACCOUNT, UUID_B):
            assert f"::add-mask::{v}" in masked
        text = redaction.redact(f"x {FIRST} {LAST} {ACCOUNT} abc-sub-123 p@example.com")
        for v in (FIRST, LAST, ACCOUNT, "abc-sub-123", "p@example.com"):
            assert v not in text

    def test_protect_identifiers_from_pumper_names(self):
        redaction.protect_identifiers(pumper={"firstName": FIRST, "lastName": LAST})
        out = redaction.redact(f"{LAST}, {FIRST}")
        assert FIRST not in out and LAST not in out

    def test_no_masks_outside_actions(self, monkeypatch):
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        out = io.StringIO()
        redaction.protect_identifiers(pumper={"firstName": FIRST, "lastName": LAST}, out=out)
        assert out.getvalue() == ""


class TestTconnectsyncLoggerLevel:
    def test_configure_logging_silences_info_decoded_jwt(self):
        stream = io.StringIO()
        root = logging.getLogger()
        old_handlers, old_level = root.handlers[:], root.level
        ts = logging.getLogger("tconnectsync")
        old_ts = ts.level
        ts.setLevel(logging.NOTSET)
        try:
            redaction.configure_logging(stream=stream)
            child = logging.getLogger("tconnectsync.api.tandemsource")
            child.info("Decoded JWT: %s", DECODED)
            child.warning("a real warning")
            assert ts.level >= logging.WARNING
        finally:
            root.handlers = old_handlers
            root.setLevel(old_level)
            ts.setLevel(old_ts)
            sys.excepthook = sys.__excepthook__
        out = stream.getvalue()
        assert "Decoded JWT" not in out
        assert "a real warning" in out

    def test_get_api_quiets_tconnectsync_before_login(self, monkeypatch):
        from unittest.mock import MagicMock

        from ingestion import client

        ts = logging.getLogger("tconnectsync")
        old = ts.level
        ts.setLevel(logging.INFO)
        seen = {}

        def fake_ctor(**kw):
            seen["level_at_login"] = ts.level
            return MagicMock()

        monkeypatch.setattr(client, "TandemSourceApi", fake_ctor)
        monkeypatch.setattr(client, "load_dotenv", lambda: None)
        try:
            client.get_api()
        finally:
            ts.setLevel(old)
        assert seen["level_at_login"] >= logging.WARNING


# ---------------------------------------------------------------------------
# Hardening: warnings capture + late-created loggers
# ---------------------------------------------------------------------------


class TestCaptureWarnings:
    def test_warning_text_is_redacted(self):
        import warnings

        stream = io.StringIO()
        root = logging.getLogger()
        old_handlers, old_level = root.handlers[:], root.level
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("always")
                redaction.register_serial(SERIAL)
                redaction.configure_logging(stream=stream)
                warnings.warn(f"deprecated thing for {SERIAL} id {UUID_A}")
        finally:
            logging.captureWarnings(False)
            root.handlers = old_handlers
            root.setLevel(old_level)
            sys.excepthook = sys.__excepthook__
        out = stream.getvalue()
        assert "deprecated thing" in out
        assert SERIAL not in out
        assert UUID_A not in out


class TestLateCreatedLoggers:
    def test_late_logger_with_own_handler_is_redacted(self, stream_logger):
        # Created AFTER install_redaction, propagate=False, plain handler.
        buf = io.StringIO()
        lg = logging.getLogger("late.created.logger")
        lg.propagate = False
        lg.setLevel(logging.INFO)
        h = logging.StreamHandler(buf)
        h.setFormatter(logging.Formatter("%(message)s"))
        lg.addHandler(h)
        redaction.register_serial(SERIAL)
        try:
            lg.info("serial %s uuid %s", SERIAL, UUID_A)
            try:
                raise RuntimeError(f"boom {UUID_B} {SERIAL}")
            except RuntimeError:
                lg.error("failed", exc_info=True)
        finally:
            lg.removeHandler(h)
        out = buf.getvalue()
        assert "Traceback" in out
        assert SERIAL not in out
        assert UUID_A not in out and UUID_B not in out

    def test_reharden_wraps_late_handler(self, stream_logger):
        buf = io.StringIO()
        lg = logging.getLogger("late.created.logger2")
        lg.propagate = False
        h = logging.StreamHandler(buf)
        lg.addHandler(h)
        try:
            redaction.install_redaction()
            assert isinstance(h.formatter, redaction.RedactingFormatter)
        finally:
            lg.removeHandler(h)

    def test_install_is_idempotent_for_record_factory(self, stream_logger):
        before = logging.getLogRecordFactory()
        redaction.install_redaction()
        redaction.install_redaction()
        assert logging.getLogRecordFactory() is before


# ---------------------------------------------------------------------------
# Linear-time redaction: regex performance + exact behavioural equivalence
# ---------------------------------------------------------------------------

import re  # noqa: E402
import time  # noqa: E402

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

# The pre-fix patterns, kept here as test-only oracles. They are correct but
# super-linear (each start position in a long run rescans to the run's end).
_OLD_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_OLD_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*")

_KB = 1024


def _sized(unit: str, n_bytes: int) -> str:
    return (unit * (n_bytes // len(unit) + 1))[:n_bytes]


_REALISTIC_UNIT = (
    "2026-10-14 03:12:45 tconnectsync.api [WARNING] Received ApiException with endpoint "
    "'api/reports/bff/pump-logs/dev42?pumperId=abc123&startDate=2026-10-13' (tries 2): "
    'HTTP 500 body={"error":"internal","detail":"upstream_timeout_retry_after_30s",'
    '"trace":"a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4"} '
)

# name -> repeating unit; each is a worst case for at least one pattern.
_ADVERSARIAL = {
    "email_chars_no_at": "a.b_c%d+e-f0",
    "many_at_no_tld": "a@b",
    "a_dot_runs": "a.a.",
    "eyj_header_no_dots": "eyJ",
    "bot_digit_runs": "bot1234567",
    "realistic_log_line": _REALISTIC_UNIT,
}


def _best_of(fn, s: str, runs: int = 3) -> float:
    best = float("inf")
    for _ in range(runs):
        t0 = time.perf_counter()
        fn(s)
        best = min(best, time.perf_counter() - t0)
    return best


class TestRedactLinearTime:
    """The old _EMAIL_RE / _JWT_TOKEN_RE took ~2.3 s on 64 KB and ~38 s on 256 KB.

    A linear implementation does each case in a few ms. Bounds are generous
    (CI runners are slow) but far below the quadratic behaviour.
    """

    @pytest.mark.parametrize("name", sorted(_ADVERSARIAL))
    def test_64kb_under_50ms(self, name):
        s = _sized(_ADVERSARIAL[name], 64 * _KB)
        assert _best_of(redaction.redact, s) < 0.050

    @pytest.mark.parametrize("name", sorted(_ADVERSARIAL))
    def test_256kb_under_200ms(self, name):
        s = _sized(_ADVERSARIAL[name], 256 * _KB)
        assert _best_of(redaction.redact, s) < 0.200


class TestEmailEdgeCases:
    @pytest.mark.parametrize(
        "text",
        [
            "first.last@example.com",  # whole line
            "first.last@example.com trailing",  # line start
            "leading first.last@example.com",  # line end
            "a\nfirst.last@example.com\nb",  # next to newlines
            "GET https://x.test/api?u=first.last@example.com&x=1",  # inside URL
            "https://first.last@example.com/path",  # userinfo in URL
            "mailto:first.last@example.com",
            "first.last+tag@example.co.uk",  # plus-addressing
            "<first_last%x-y@sub-domain.example.org>",
        ],
    )
    def test_email_redacted(self, text):
        out = redaction.redact(text)
        assert "first" not in out and "@" not in out
        assert "<email>" in out

    def test_surrounding_text_preserved(self):
        out = redaction.redact("GET https://x.test/api?u=first.last@example.com&x=1")
        assert out == "GET https://x.test/api?u=<email>&x=1"

    def test_back_to_back_emails_after_partial_domain(self):
        # The first match ends at "c"; the old regex resumed mid-run at "_d".
        assert redaction.redact("a@b.c_d@e.com") == "<email><email>"

    def test_long_local_part_fully_redacted(self):
        local = "x" * 300
        assert redaction.redact(f"to {local}@example.com ok") == "to <email> ok"

    def test_long_domain_fully_redacted(self):
        domain = ".".join(["label" * 20] * 10) + ".com"
        assert redaction.redact(f"to a@{domain} ok") == "to <email> ok"

    def test_no_tld_untouched(self):
        assert redaction.redact("user@localhost and a@b") == "user@localhost and a@b"


class TestJwtEdgeCases:
    def test_real_length_jwt_redacted(self):
        payload = "eyJ" + "A" * 1500
        tok = f"eyJhbGciOiJSUzI1NiJ9.{payload}.{'s' * 342}"
        assert redaction.redact(f"Authorization: Bearer {tok}\n") == "Authorization: Bearer <jwt>\n"

    def test_jwt_embedded_after_word_chars(self):
        assert redaction.redact(f"xx{FAKE_JWT} y") == "xx<jwt> y"


_EMAIL_ALPHABET = st.sampled_from(list("ab.-_+%@ \n") + ["eyJ", ".com", "x@y.z"])
_JWT_ALPHABET = st.sampled_from(list("ab.-_ =") + ["eyJ", "eyJa.", ".b."])


class TestEquivalenceWithOldPatterns:
    """New substitutions must be byte-identical to the old regexes."""

    @settings(max_examples=2000, deadline=None)
    @given(st.lists(_EMAIL_ALPHABET, max_size=40).map("".join))
    def test_email_matches_old(self, s):
        assert redaction._redact_emails(s) == _OLD_EMAIL_RE.sub("<email>", s)

    @settings(max_examples=2000, deadline=None)
    @given(st.lists(_JWT_ALPHABET, max_size=40).map("".join))
    def test_jwt_matches_old(self, s):
        assert redaction._redact_jwts(s) == _OLD_JWT_RE.sub("<jwt>", s)
