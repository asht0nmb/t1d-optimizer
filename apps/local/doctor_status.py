"""Read-only pipeline health summary for the Streamlit sidebar."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

from core.storage.parquet import PARQUET_FILES, PIPELINE_VERSION_FILENAME
from ingestion.pipeline_version import PIPELINE_VERSION


class DoctorStatus(TypedDict):
    code_version: int
    on_disk_version: int | None
    parquet_count: int
    present_tables: list[str]
    staleness_message: str | None
    ok: bool


def collect_doctor_status(processed_root: Path) -> DoctorStatus:
    """Summarize version sidecar and parquet presence under ``processed_root``."""
    present: list[str] = []
    for name, filename in PARQUET_FILES.items():
        if (processed_root / filename).exists():
            present.append(name)

    on_disk: int | None = None
    version_path = processed_root / PIPELINE_VERSION_FILENAME
    if version_path.exists():
        try:
            payload = json.loads(version_path.read_text())
            raw = payload.get("version")
            if raw is not None:
                on_disk = int(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            on_disk = None

    staleness: str | None = None
    if on_disk is None and present:
        staleness = (
            "On-disk data is unversioned. Run "
            "`uv run python main.py fetch --clean` to regenerate."
        )
    elif on_disk is not None and on_disk != PIPELINE_VERSION:
        staleness = (
            f"Pipeline version mismatch: on-disk v{on_disk}, "
            f"code v{PIPELINE_VERSION}. Run fetch --clean."
        )

    ok = bool(present) and staleness is None
    return DoctorStatus(
        code_version=PIPELINE_VERSION,
        on_disk_version=on_disk,
        parquet_count=len(present),
        present_tables=present,
        staleness_message=staleness,
        ok=ok,
    )
