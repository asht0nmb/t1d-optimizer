"""Detection engine package.

Typed, validated config access lives in `detection.config`. Downstream
detection modules (anomaly, meals, clustering) read config exclusively
through `get_config()` — no module should re-open the YAML file.
"""

from .config import AppConfig, get_config, load_config

__all__ = ["AppConfig", "get_config", "load_config"]
