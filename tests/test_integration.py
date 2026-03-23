"""Integration tests — gated behind the 'integration' marker and env var."""

import os

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not os.getenv("TCONNECT_EMAIL"),
    reason="Requires TCONNECT_EMAIL env var (real API credentials)",
)
class TestIntegrationPipeline:
    def test_single_day_fetch(self):
        """TODO: Fetch a single known day, verify output against manually verified values."""
        pytest.skip("Not yet implemented — needs manually verified reference data")
