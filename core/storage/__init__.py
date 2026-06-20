"""Storage Protocol + concrete implementations.

The :class:`Storage` Protocol (``core.storage.protocol``) is the
backend-agnostic data layer every caller talks to. Three concrete
implementations:

* :class:`InMemoryStorage` — in-process dicts; used by tests and
  one-shot scripts.
* :class:`ParquetStorage` — local files; default for the OSS shell.
* :class:`SupabaseStorage` — Postgres via psycopg2; used by the
  personal stack (Vercel + GitHub Actions + dashboard).

The :mod:`core.storage._postgres_converters` module holds the
pandas-row → Postgres-tuple converters that ``SupabaseStorage`` and the
one-shot bootstrap script share. The leading underscore flags it as a
Postgres-impl-only module; outside callers go through
``SupabaseStorage``.
"""

from typing import TYPE_CHECKING, Any

from core.storage.memory import InMemoryStorage
from core.storage.parquet import ParquetStorage
from core.storage.protocol import Storage
from core.storage.records import (
    AlertRecord,
    DetectionResult,
    FetchState,
    UpsertResult,
)

if TYPE_CHECKING:
    # Imported only for type checkers; never at runtime so the OSS shell
    # (ParquetStorage / InMemoryStorage) does not require psycopg2.
    from core.storage.supabase import SupabaseStorage


def __getattr__(name: str) -> Any:
    """PEP 562 lazy attribute access.

    ``SupabaseStorage`` lives in ``core.storage.supabase``, which raises
    ImportError at module load if psycopg2 is absent. Importing it eagerly here
    would force every ``from core.storage import ParquetStorage`` (the OSS /
    local path) to require psycopg2. Resolving it lazily keeps the OSS import
    psycopg2-free while ``from core.storage import SupabaseStorage`` still works.
    """
    if name == "SupabaseStorage":
        from core.storage.supabase import SupabaseStorage

        return SupabaseStorage
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AlertRecord",
    "DetectionResult",
    "FetchState",
    "InMemoryStorage",
    "ParquetStorage",
    "Storage",
    "SupabaseStorage",
    "UpsertResult",
]
