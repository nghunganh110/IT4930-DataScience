"""
Tests for config validation.

Covers:
- Valid configuration passes
- Invalid source names raise ValueError
- radius_m > 25 km raises ValueError
- Latitude/longitude bounds
- Timeout and airnow_max_hours bounds
- Date ordering
"""

import pytest
from datetime import date

from airguard_data.config import FeasibilityConfig, OPENAQ_MAX_RADIUS_M


def _base_config(**overrides):
    defaults = dict(
        sources=["openaq", "airnow", "hanoi_portal"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        latitude=21.0285,
        longitude=105.8542,
        radius_m=25_000,
        timeout_seconds=30,
        airnow_max_hours=24,
        output_dir="/tmp/test",
        report_path="/tmp/test.md",
    )
    defaults.update(overrides)
    return FeasibilityConfig(**defaults)


class TestConfigValidation:
    def test_valid_config_passes(self):
        cfg = _base_config()
        cfg.validate()  # should not raise

    def test_unknown_source_raises(self):
        cfg = _base_config(sources=["openaq", "not_a_source"])
        with pytest.raises(ValueError, match="Unknown source"):
            cfg.validate()

    def test_empty_sources_raises(self):
        cfg = _base_config(sources=[])
        with pytest.raises(ValueError, match="At least one source"):
            cfg.validate()

    def test_start_after_end_raises(self):
        cfg = _base_config(start_date=date(2024, 2, 1), end_date=date(2024, 1, 1))
        with pytest.raises(ValueError, match="start_date"):
            cfg.validate()

    def test_radius_above_openaq_limit_raises(self):
        cfg = _base_config(radius_m=OPENAQ_MAX_RADIUS_M + 1)
        with pytest.raises(ValueError, match="radius_m"):
            cfg.validate()

    def test_radius_at_limit_passes(self):
        cfg = _base_config(radius_m=OPENAQ_MAX_RADIUS_M)
        cfg.validate()  # should not raise

    def test_negative_radius_raises(self):
        cfg = _base_config(radius_m=-1)
        with pytest.raises(ValueError, match="radius_m"):
            cfg.validate()

    def test_invalid_latitude_raises(self):
        cfg = _base_config(latitude=91.0)
        with pytest.raises(ValueError, match="latitude"):
            cfg.validate()

    def test_invalid_longitude_raises(self):
        cfg = _base_config(longitude=181.0)
        with pytest.raises(ValueError, match="longitude"):
            cfg.validate()

    def test_zero_timeout_raises(self):
        cfg = _base_config(timeout_seconds=0)
        with pytest.raises(ValueError, match="timeout_seconds"):
            cfg.validate()

    def test_zero_airnow_max_hours_raises(self):
        cfg = _base_config(airnow_max_hours=0)
        with pytest.raises(ValueError, match="airnow_max_hours"):
            cfg.validate()
