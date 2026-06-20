"""Importing the OSS storage path must not pull in psycopg2.

``core.storage.supabase`` raises ImportError at module load if psycopg2 is
absent. The OSS / local shell only ever needs ``ParquetStorage`` /
``InMemoryStorage``, so importing ``core.storage`` and accessing those names
must NOT transitively import ``core.storage.supabase`` (and therefore must not
require psycopg2). ``SupabaseStorage`` is exposed lazily via PEP 562
module-level ``__getattr__``.
"""

from __future__ import annotations

import subprocess
import sys


def test_parquet_import_does_not_load_supabase_module() -> None:
    """A fresh interpreter that imports only the OSS path leaves
    ``core.storage.supabase`` unloaded."""
    code = (
        "import sys\n"
        "from core.storage import ParquetStorage, InMemoryStorage\n"
        "assert 'core.storage.supabase' not in sys.modules, "
        "'core.storage.supabase was imported eagerly'\n"
        "assert ParquetStorage is not None\n"
        "assert InMemoryStorage is not None\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_supabase_storage_still_importable_lazily() -> None:
    """``from core.storage import SupabaseStorage`` keeps working (it imports
    the submodule on demand)."""
    code = (
        "from core.storage import SupabaseStorage\n"
        "import sys\n"
        "assert 'core.storage.supabase' in sys.modules\n"
        "assert SupabaseStorage.__name__ == 'SupabaseStorage'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_unknown_attribute_raises_attribute_error() -> None:
    import core.storage as storage_pkg

    try:
        storage_pkg.NotARealName  # noqa: B018
    except AttributeError:
        return
    raise AssertionError("expected AttributeError for unknown attribute")
