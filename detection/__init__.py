"""Detection engine package.

Typed, validated config access lives in `detection.config`. `daily_features`
in `detection.features` is the patterns-layer foundation v2 will build on.
Reference implementation from v1 is preserved under `detection.legacy/` —
not maintained, not imported from production code. See
`docs/plans/2026-05-05-detection-rework-and-surfaces.md` for v2 direction.
"""

from .config import AppConfig, get_config, load_config

__all__ = ["AppConfig", "get_config", "load_config"]
