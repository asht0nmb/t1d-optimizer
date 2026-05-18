"""Load parquet tables via :class:`core.storage.protocol.Storage` for the local shell."""

from __future__ import annotations

import pandas as pd

from core.storage.parquet import PARQUET_FILES
from core.storage.protocol import Storage
from detection.config import AppConfig, get_config
from ingestion.view_data import ViewMode, ensure_enriched, strip_enriched_columns


def load_view_frames(
    storage: Storage,
    *,
    view: ViewMode = "original",
    config: AppConfig | None = None,
) -> dict[str, pd.DataFrame]:
    """Read all logical tables from ``storage`` and project into ``view`` mode."""
    frames: dict[str, pd.DataFrame] = {}
    for name in PARQUET_FILES:
        frames[name] = storage.read_all_table(name)

    if view == "original":
        for name in list(frames):
            frames[name] = strip_enriched_columns(name, frames[name])
        return frames

    if config is None:
        config = get_config()
    return ensure_enriched(frames, config)
