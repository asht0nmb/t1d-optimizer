"""Shared pytest fixtures."""

import logging

import pytest

from detection.config import get_config, load_config


@pytest.fixture(autouse=True)
def _restore_logging_globals():
    """ingestion.redaction installs a process-wide LogRecordFactory and enables
    warnings capture; undo both so they never leak between tests."""
    factory = logging.getLogRecordFactory()
    yield
    logging.setLogRecordFactory(factory)
    logging.captureWarnings(False)


@pytest.fixture
def default_config():
    """Load the checked-in `config/user_config.yaml` as an `AppConfig`.

    Clears the `get_config` lru_cache so tests that mutate or swap the YAML
    elsewhere in the session don't leak a stale cached instance into this
    fixture.
    """
    get_config.cache_clear()
    return load_config()
