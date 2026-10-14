"""Lint-style guard: no non-test code combines psycopg2 readonly + autocommit.

With ``autocommit`` on, psycopg2 implements ``readonly=True`` as a
session-level ``SET default_transaction_read_only = on``. On the Supabase
transaction-mode pooler that session state leaks onto a shared backend and
breaks every other client's writes (incident 2026-10-14). ``readonly=True``
WITHOUT autocommit is safe: psycopg2 then emits ``BEGIN READ ONLY`` per
transaction and sends no session-level SET.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_SET_SESSION_CALL = re.compile(r"set_session\s*\(([^)]*)\)", re.DOTALL)
_READONLY_TRUE = re.compile(
    r"set_session\s*\([^)]*readonly\s*=\s*True|\.readonly\s*=\s*True", re.DOTALL
)
_AUTOCOMMIT_TRUE = re.compile(r"autocommit\s*=\s*True")


def find_violations(source: str) -> list[str]:
    """Return human-readable reasons ``source`` risks a pooler session leak."""
    reasons: list[str] = []
    for m in _SET_SESSION_CALL.finditer(source):
        args = m.group(1)
        if re.search(r"readonly\s*=\s*True", args) and re.search(
            r"autocommit\s*=\s*True", args
        ):
            reasons.append(f"set_session({args.strip()}) combines readonly+autocommit")
    if _READONLY_TRUE.search(source) and _AUTOCOMMIT_TRUE.search(source):
        reasons.append("file sets readonly=True and autocommit=True")
    return reasons


def _tracked_non_test_python_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    return [REPO_ROOT / p for p in out if not p.startswith("tests/")]


class TestDetector:
    def test_flags_incident_call(self):
        assert find_violations("conn.set_session(readonly=True, autocommit=True)")

    def test_flags_reversed_kwargs(self):
        assert find_violations("conn.set_session(autocommit=True, readonly=True)")

    def test_flags_split_across_statements(self):
        src = "conn.autocommit = True\nconn.set_session(readonly=True)\n"
        assert find_violations(src)

    def test_flags_readonly_attribute(self):
        src = "conn.readonly = True\nconn.autocommit = True\n"
        assert find_violations(src)

    def test_allows_readonly_without_autocommit(self):
        assert not find_violations("conn.set_session(readonly=True)")

    def test_allows_autocommit_without_readonly(self):
        assert not find_violations("conn.autocommit = True\n")


def test_no_non_test_code_combines_readonly_and_autocommit():
    files = _tracked_non_test_python_files()
    assert files, "git ls-files returned nothing; is this a git checkout?"
    offenders = {
        str(p.relative_to(REPO_ROOT)): reasons
        for p in files
        if p.exists()
        and (reasons := find_violations(p.read_text(encoding="utf-8")))
    }
    assert offenders == {}, (
        "psycopg2 readonly+autocommit issues a session-level SET that leaks "
        f"across the transaction pooler: {offenders}"
    )
