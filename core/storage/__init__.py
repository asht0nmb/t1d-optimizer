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

from core.storage.memory import InMemoryStorage
from core.storage.parquet import ParquetStorage
from core.storage.protocol import Storage
from core.storage.records import (
    AlertRecord,
    DetectionResult,
    FetchState,
    UpsertResult,
)
from core.storage.supabase import SupabaseStorage

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
