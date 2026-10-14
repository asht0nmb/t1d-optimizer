"""Tests for scripts/ci_log_gate.py — the default-deny CI output gate.

Every test runs the gate exactly as CI does: by file path, from a cwd that is
NOT the repo root, wrapping a real child process. Child log lines are produced
by the real ``configure_logging`` / ``basicConfig`` calls the entrypoints use,
not hand-typed, so a format drift in an entrypoint breaks these tests.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GATE = REPO / "scripts" / "ci_log_gate.py"

# Fake identifiers. Never real values.
FAKE_UUID = "12345678-abcd-4ef0-9abc-1234567890ab"
FAKE_EMAIL = "someone@example.com"
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"

SMOKE_FMT = "%(asctime)s %(levelname)s %(name)s — %(message)s"


def run_gate(child_code: str, tmp_path: Path, *extra_args: str, env=None) -> subprocess.CompletedProcess:
    """Run the gate around ``python -c child_code`` from a non-repo cwd."""
    script = tmp_path / "child.py"
    script.write_text(textwrap.dedent(child_code))
    cmd = [sys.executable, str(GATE), *extra_args, "--", sys.executable, str(script)]
    child_env = None
    if env is not None:
        import os

        child_env = {**os.environ, **env}
    return subprocess.run(
        cmd,
        cwd=tmp_path,
        capture_output=True,
        timeout=60,
        env=child_env,
    )


def out_lines(proc: subprocess.CompletedProcess) -> list[str]:
    return proc.stdout.decode("utf-8", errors="replace").splitlines()


def _child_prelude(fmt_call: str) -> str:
    # The child imports the real redaction module from the repo, like the
    # entrypoints do (redaction.py itself is stdlib-only).
    return f"""
import importlib.util, logging, sys
spec = importlib.util.spec_from_file_location("redaction", {str(REPO / "ingestion" / "redaction.py")!r})
redaction = importlib.util.module_from_spec(spec); spec.loader.exec_module(redaction)
{fmt_call}
"""


SMOKE_PRELUDE = _child_prelude(
    f"redaction.configure_logging(stream=sys.stdout, fmt={SMOKE_FMT!r}, datefmt=None)"
)
SYNC_PRELUDE = _child_prelude("redaction.configure_logging(stream=sys.stdout)")
MEAL_RISE_PRELUDE = _child_prelude(
    "logging.basicConfig(level=logging.INFO, stream=sys.stdout, "
    "format='%(asctime)s %(name)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')"
)


# --------------------------------------------------------------------------
# Pass-through of allowlisted loggers (all three real entrypoint formats)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("prelude", [SMOKE_PRELUDE, SYNC_PRELUDE, MEAL_RISE_PRELUDE],
                         ids=["smoke-fmt", "sync-fmt", "meal-rise-fmt"])
@pytest.mark.parametrize("name", ["ingestion.client", "__main__", "ci_tandem_smoke",
                                  "meal_rise_cron", "scripts.sync_tandem_to_supabase",
                                  "apps.personal.cron.x", "core.storage.parquet"])
def test_allowlisted_logger_lines_pass(prelude, name, tmp_path):
    proc = run_gate(prelude + f"""
logging.getLogger({name!r}).info("Pump …3861: 1/1 chunks fetched, 0 failed.")
""", tmp_path)
    assert proc.returncode == 0, proc.stdout
    lines = out_lines(proc)
    passed = [l for l in lines if "chunks fetched" in l]
    assert len(passed) == 1
    assert name in passed[0]
    assert "[gate] suppressed 0 lines" in lines[-1]


def test_meal_rise_format_without_redaction_is_redacted_by_gate(tmp_path):
    # meal-rise uses plain basicConfig (no in-process redaction): the gate's
    # second redaction pass is the only thing scrubbing these.
    proc = run_gate(MEAL_RISE_PRELUDE + f"""
logging.getLogger("meal_rise_cron").info("user {FAKE_EMAIL} device {FAKE_UUID} tok {FAKE_JWT}")
""", tmp_path)
    text = proc.stdout.decode()
    assert "meal_rise_cron" in text
    assert FAKE_EMAIL not in text
    assert FAKE_UUID not in text
    assert "eyJ" not in text
    assert "<email>" in text and "<id>" in text and "<jwt>" in text


def test_add_mask_values_are_redacted_from_later_lines(tmp_path):
    # Values the child masked are also scrubbed by the gate's own pass.
    proc = run_gate(MEAL_RISE_PRELUDE + """
print("::add-mask::Zaphodbeeblebrox", flush=True)
logging.getLogger("meal_rise_cron").info("hello Zaphodbeeblebrox")
""", tmp_path)
    lines = out_lines(proc)
    assert "::add-mask::Zaphodbeeblebrox" in lines
    logged = [l for l in lines if "meal_rise_cron" in l]
    assert logged and "Zaphodbeeblebrox" not in logged[0]


def test_allowlist_matches_on_dot_boundary_only(tmp_path):
    proc = run_gate(SYNC_PRELUDE + """
logging.getLogger("ingestionx.evil").warning("should not pass")
logging.getLogger("apps_fake").warning("should not pass")
logging.getLogger("__main__x").warning("should not pass")
""", tmp_path)
    assert "should not pass" not in proc.stdout.decode()
    assert "[gate] suppressed 3 lines" in out_lines(proc)[-1]


# --------------------------------------------------------------------------
# Suppression of everything else
# --------------------------------------------------------------------------


def test_third_party_and_raw_output_suppressed(tmp_path):
    proc = run_gate(SYNC_PRELUDE + f"""
import warnings
logging.getLogger("tconnectsync").setLevel(logging.INFO)
logging.getLogger("tconnectsync.api").warning("GET https://x/pump-logs/abc?pumperId=abc")
logging.getLogger("tconnectsync.api").info('Decoded JWT: {{"given_name": "Ford"}}')
logging.getLogger("urllib3.connectionpool").warning("Retrying (Retry(total=2)) after connection broken")
logging.getLogger("root").warning("root logger line")
warnings.warn("library deprecation text")
print("bare print from somewhere")
print("Decoded JWT: {{'sub': '{FAKE_UUID}'}}")
sys.stderr.write("send: b'POST /token HTTP/1.1'\\n")
""", tmp_path)
    text = proc.stdout.decode()
    for needle in ("tconnectsync", "Decoded JWT", "urllib3", "root logger line",
                   "deprecation", "bare print", "send:", FAKE_UUID, "Ford"):
        assert needle not in text, needle
    lines = out_lines(proc)
    assert lines[-1].startswith("[gate] suppressed ")
    assert int(lines[-1].split()[2]) >= 8
    assert len(lines) == 1


def test_other_workflow_commands_suppressed(tmp_path):
    proc = run_gate("""
print("::warning::pii in annotation", flush=True)
print("::stop-commands::abc123", flush=True)
print("::set-output name=x::y", flush=True)
""", tmp_path)
    text = proc.stdout.decode()
    assert "::warning::" not in text
    assert "::stop-commands::" not in text
    assert "::set-output" not in text
    assert "[gate] suppressed 3 lines" in out_lines(proc)[-1]


def test_control_characters_stripped_from_passed_lines(tmp_path):
    proc = run_gate(SYNC_PRELUDE + r"""
logging.getLogger("ingestion.client").info("ok \x1b[2Kcolour\rover")
""", tmp_path)
    passed = [l for l in out_lines(proc) if "ingestion.client" in l]
    assert passed
    assert "\x1b" not in passed[0] and "\r" not in passed[0]


# --------------------------------------------------------------------------
# add-mask
# --------------------------------------------------------------------------


def test_add_mask_passes_through_unchanged(tmp_path):
    # Values that redact() WOULD rewrite must reach the runner raw.
    proc = run_gate(f"""
print("::add-mask::{FAKE_UUID}", flush=True)
print("::add-mask::{FAKE_EMAIL}", flush=True)
print("::add-mask::Arthur Dent", flush=True)
""", tmp_path)
    lines = out_lines(proc)
    assert f"::add-mask::{FAKE_UUID}" in lines
    assert f"::add-mask::{FAKE_EMAIL}" in lines
    assert "::add-mask::Arthur Dent" in lines
    assert "[gate] suppressed 0 lines" in lines[-1]


def test_add_mask_from_real_emitter(tmp_path):
    proc = run_gate(SYNC_PRELUDE + f"""
redaction.emit_github_masks(["{FAKE_UUID}"])
""", tmp_path, env={"GITHUB_ACTIONS": "true"})
    assert f"::add-mask::{FAKE_UUID}" in out_lines(proc)


# --------------------------------------------------------------------------
# Tracebacks
# --------------------------------------------------------------------------


def test_uncaught_traceback_collapses_to_one_redacted_line(tmp_path):
    proc = run_gate(f"""
def inner():
    raise ValueError("bad device {FAKE_UUID} for {FAKE_EMAIL}")
inner()
""", tmp_path)
    assert proc.returncode == 1
    lines = out_lines(proc)
    gate_lines = [l for l in lines if l.startswith("[gate] ValueError")]
    assert gate_lines == ["[gate] ValueError: bad device <id> for <email>"]
    text = proc.stdout.decode()
    assert "Traceback" not in text
    assert "inner()" not in text
    assert len(lines) == 2  # collapsed line + summary


def test_chained_traceback_collapses_to_final_exception(tmp_path):
    proc = run_gate("""
try:
    {}["k"]
except KeyError as e:
    raise RuntimeError("wrapped") from e
""", tmp_path)
    lines = out_lines(proc)
    assert [l for l in lines if not l.startswith("[gate] suppressed")] == [
        "[gate] RuntimeError: wrapped"
    ]


def test_logger_exception_header_passes_traceback_collapses(tmp_path):
    proc = run_gate(SYNC_PRELUDE + """
try:
    raise OSError("disk sad")
except OSError:
    logging.getLogger("__main__").exception("sync failed")
""", tmp_path)
    lines = out_lines(proc)
    assert any("__main__" in l and "sync failed" in l for l in lines)
    assert "[gate] OSError: disk sad" in lines
    assert "Traceback" not in proc.stdout.decode()


def test_truncated_traceback_at_eof_is_suppressed(tmp_path):
    proc = run_gate("""
print("Traceback (most recent call last):")
print('  File "x.py", line 1, in <module>')
""", tmp_path)
    lines = out_lines(proc)
    assert lines == ["[gate] suppressed 2 lines from non-allowlisted sources"]


# --------------------------------------------------------------------------
# Exit codes, signals, robustness
# --------------------------------------------------------------------------


@pytest.mark.parametrize("code", [0, 1, 2])
def test_exit_code_propagates(code, tmp_path):
    proc = run_gate(f"import sys; sys.exit({code})", tmp_path)
    assert proc.returncode == code


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_signal_death_propagates(tmp_path):
    proc = run_gate("import os, signal; os.kill(os.getpid(), signal.SIGTERM)", tmp_path)
    assert proc.returncode in (-signal.SIGTERM, 128 + signal.SIGTERM)


def test_binary_garbage_does_not_crash(tmp_path):
    proc = run_gate(SYNC_PRELUDE + r"""
sys.stdout.flush()
sys.stdout.buffer.write(b"\xff\xfe\x00\x80garbage\n" + bytes(range(256)) + b"\n")
sys.stdout.buffer.flush()
logging.getLogger("ingestion.client").info("still alive")
""", tmp_path)
    assert proc.returncode == 0
    lines = out_lines(proc)
    assert any("still alive" in l for l in lines)
    assert "[gate] suppressed 3 lines" in lines[-1]  # range(256) contains a newline


def test_streams_line_by_line(tmp_path):
    # The gate must emit a passed line before the child exits.
    script = tmp_path / "child.py"
    script.write_text(textwrap.dedent(SYNC_PRELUDE + """
import time
logging.getLogger("ingestion.client").info("first")
time.sleep(30)
"""))
    p = subprocess.Popen(
        [sys.executable, str(GATE), "--", sys.executable, str(script)],
        cwd=tmp_path, stdout=subprocess.PIPE,
    )
    try:
        line = p.stdout.readline().decode()
        assert "first" in line
        assert p.poll() is None
    finally:
        p.terminate()
        p.wait(timeout=10)


def test_suppressed_count_summary(tmp_path):
    proc = run_gate(SYNC_PRELUDE + """
print("a"); print("b"); print("c")
logging.getLogger("ingestion.client").info("kept")
""", tmp_path)
    lines = out_lines(proc)
    assert lines[-1] == "[gate] suppressed 3 lines from non-allowlisted sources"


def test_missing_command_is_usage_error(tmp_path):
    proc = subprocess.run([sys.executable, str(GATE)], cwd=tmp_path, capture_output=True)
    assert proc.returncode == 2


def test_unlaunchable_command_fails_closed(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(GATE), "--", str(tmp_path / "does-not-exist")],
        cwd=tmp_path, capture_output=True,
    )
    assert proc.returncode != 0
    assert "does-not-exist" not in proc.stdout.decode()


def test_internal_gate_error_fails_closed(tmp_path):
    # A crash inside the line processor: output suppressed, exit non-zero
    # even though the child succeeded, and the child is drained (no hang).
    proc = run_gate(SYNC_PRELUDE + """
for i in range(20000):
    print("x" * 100)
logging.getLogger("ingestion.client").info("after crash")
""", tmp_path, env={"CI_LOG_GATE_TEST_CRASH": "1"})
    assert proc.returncode != 0
    text = proc.stdout.decode()
    assert "after crash" not in text
    assert out_lines(proc) == ["[gate] internal error: RuntimeError; output suppressed"]
