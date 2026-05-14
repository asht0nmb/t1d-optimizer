"""Storage-agnostic library shared by every deployment shell.

Modules under `core/` define the Storage Protocol, the table-identity
registry, and the typed metadata records that every implementation
(parquet, Supabase, in-memory) speaks. Code in `core/` MAY import from
stdlib, pandas, numpy, pydantic, and typing/Protocol. It MUST NOT import
from `ingestion/`, `scripts/`, backend SDKs (psycopg2, supabase-py), or
deployment-specific frameworks (Streamlit, FastAPI, Vercel, Telegram).

Backend-specific concrete code lives in `core/storage/parquet.py` and
`core/storage/supabase.py`; those files are the ONLY ones allowed to
import their respective backend SDKs.
"""
