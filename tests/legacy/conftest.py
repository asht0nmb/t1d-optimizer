"""Re-export shared fixtures for legacy detection tests.

Tests in this directory must continue to use the project-wide
`default_config` fixture defined in `tests/conftest.py`.
"""

from tests.conftest import default_config  # noqa: F401
