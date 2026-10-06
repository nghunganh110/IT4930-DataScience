"""
Tests for the AirNow public archive adapter.

Covers:
- URL construction for the correct GMT hours
- Request cap (airnow_max_hours)
- Pipe-delimited parsing: correct fields extracted
- Hanoi matching: only Hanoi rows selected; generic DoS rows from other cities excluded
- GMT → UTC storage, local timestamp from GMT offset (positive and negative offsets)
- Negative readings preserved but flagged is_valid=False
- Malformed rows counted but do not crash
- 404 archive hours counted as missing
- Unrecognized units rejected and counted
- Sample remains ineligible; no annual claim
"""

import pytest
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from airguard_data.config import FeasibilityConfig
from airguard_data.contracts import SourceStatus
from airguard_data.http import RedactedException
from airguard_data.sources.airnow import AirNowAdapter, _is_hanoi_row, _local_ts_from_gmt_offset, _build_hour_url

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "airnow"


def _make_config(**overrides):
    defaults = dict(
        sources=["airnow"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 1),
        latitude=21.0285,
        longitude=105.8542,
        radius_m=25_000,
        timeout_seconds=5,
        airnow_max_hours=1,
        output_dir="/tmp/test_airnow",
        report_path="/tmp/test_airnow.md",
    )
    defaults.update(overrides)
    return FeasibilityConfig(**defaults)


class TestAirNowHanoiMatching:
    def test_hanoi_in_site_name_matches(self):
        assert _is_hanoi_row("Hanoi DOS", "US DOS") is True
        assert _is_hanoi_row("Hanoi", "anything") is True
        assert _is_hanoi_row("HANOI CENTRAL", "SomeSource") is True

    def test_vietnam_dos_matches(self):
        assert _is_hanoi_row("Vietnam Embassy", "US Department of State") is True
        assert _is_hanoi_row("Vietnam", "dos") is True

    def test_beijing_dos_does_not_match(self):
        assert _is_hanoi_row("Beijing DOS", "US DOS") is False

    def test_generic_dos_other_city_does_not_match(self):
        assert _is_hanoi_row("New Delhi", "US DOS") is False
        assert _is_hanoi_row("Jakarta", "Department of State") is False

    def test_empty_strings_do_not_match(self):
        assert _is_hanoi_row("", "") is False


class TestAirNowTimestampConversion:
    def test_negative_gmt_offset(self):
        """GMT-7: 2024-01-01 00:00 UTC → 2023-12-31 17:00 local"""
        ts_utc = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        ts_local = _local_ts_from_gmt_offset(ts_utc, "-7")
        assert ts_local.utcoffset() == timedelta(hours=-7)
        assert ts_local.hour == 17  # 00:00 - 7h = 17:00 previous day

    def test_positive_gmt_offset(self):
        """GMT+7: 2024-01-01 00:00 UTC → 2024-01-01 07:00 local"""
        ts_utc = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        ts_local = _local_ts_from_gmt_offset(ts_utc, "7")
        assert ts_local.utcoffset() == timedelta(hours=7)
        assert ts_local.hour == 7

    def test_zero_offset(self):
        ts_utc = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
        ts_local = _local_ts_from_gmt_offset(ts_utc, "0")
        assert ts_local.hour == 12

    def test_invalid_offset_defaults_to_utc(self):
        ts_utc = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        ts_local = _local_ts_from_gmt_offset(ts_utc, "bad")
        assert ts_local.utcoffset() == timedelta(0)


class TestAirNowURLConstruction:
    def test_url_format(self):
        dt = datetime(2024, 9, 15, 13, 0, tzinfo=timezone.utc)
        url = _build_hour_url(dt)
        assert "2024" in url
        assert "20240915" in url
        assert "2024091513" in url
        assert url.endswith(".dat")
        assert "airnowtech" in url or "airnow" in url


class TestAirNowParsing:
    def test_fixture_based_parsing(self):
        """Unit test using fixture files via HttpClient offline mode."""
        config = _make_config(offline_fixtures=str(FIXTURE_DIR))
        result = AirNowAdapter().probe(config)

        assert result.source == "airnow"
        # Should have parsed 1 Hanoi row, Beijing excluded
        assert not result.observations.empty
        df = result.observations
        assert len(df) == 1
        assert df.iloc[0]["location_id"] == "HanDOS"
        assert df.iloc[0]["pm2_5_ug_m3"] == pytest.approx(12.3, rel=1e-3)
        assert str(df.iloc[0]["unit_original"]) == "ug/m3"

    def test_mock_based_parsing(self):
        """Mock-based parsing test for direct control of response text."""
        config = _make_config()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = (
            "01/01/24|00:00|123|Hanoi DOS|-7|PM2.5|UG/M3|12.3|US DOS\n"
            "01/01/24|00:00|456|Goose Bay|-4|OZONE|PPB|37|Network\n"
            "01/01/24|00:00|789|Beijing DOS|-8|PM2.5|UG/M3|55.0|US DOS\n"
        )
        mock_resp.headers = {}
        with patch("airguard_data.http.HttpClient.get", return_value=mock_resp):
            result = AirNowAdapter().probe(config)

        assert not result.observations.empty
        assert len(result.observations) == 1  # only Hanoi
        row = result.observations.iloc[0]
        assert row["location_id"] == "123"
        assert row["pm2_5_ug_m3"] == pytest.approx(12.3)
        assert str(row["unit_original"]) == "ug/m3"

    def test_unit_normalization_UG_M3(self):
        config = _make_config()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "01/01/24|00:00|123|Hanoi DOS|-7|PM2.5|UG/M3|10.0|US DOS\n"
        mock_resp.headers = {}
        with patch("airguard_data.http.HttpClient.get", return_value=mock_resp):
            result = AirNowAdapter().probe(config)
        assert str(result.observations.iloc[0]["unit_original"]) == "ug/m3"

    def test_negative_concentration_marked_invalid(self):
        config = _make_config()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "01/01/24|00:00|123|Hanoi DOS|-7|PM2.5|UG/M3|-5.0|US DOS\n"
        mock_resp.headers = {}
        with patch("airguard_data.http.HttpClient.get", return_value=mock_resp):
            result = AirNowAdapter().probe(config)
        df = result.observations
        assert len(df) == 1
        assert df.iloc[0]["is_valid"] is False or df.iloc[0]["is_valid"] == False

    def test_latitude_longitude_are_null(self):
        """AirNow .dat does not include coordinates; must be null, not fabricated."""
        config = _make_config()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "01/01/24|00:00|123|Hanoi DOS|-7|PM2.5|UG/M3|12.3|US DOS\n"
        mock_resp.headers = {}
        with patch("airguard_data.http.HttpClient.get", return_value=mock_resp):
            result = AirNowAdapter().probe(config)
        import pandas as pd
        row = result.observations.iloc[0]
        assert pd.isna(row["latitude"])
        assert pd.isna(row["longitude"])

    def test_timestamp_stored_as_utc(self):
        """timestamp_utc must be UTC-aware."""
        config = _make_config()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "01/01/24|00:00|123|Hanoi DOS|-7|PM2.5|UG/M3|12.3|US DOS\n"
        mock_resp.headers = {}
        with patch("airguard_data.http.HttpClient.get", return_value=mock_resp):
            result = AirNowAdapter().probe(config)
        df = result.observations
        ts = df.iloc[0]["timestamp_utc"]
        assert ts.tzinfo is not None  # timezone-aware
        assert str(ts.tzinfo) in ("UTC", "utc") or "UTC" in str(ts.tzinfo).upper()

    def test_404_hour_counted_as_missing(self):
        config = _make_config()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("airguard_data.http.HttpClient.get", return_value=mock_resp):
            result = AirNowAdapter().probe(config)

        assert result.observations.empty
        assert result.metadata["hours_missing_archive"] == 1

    def test_request_cap_applied(self):
        """With airnow_max_hours=2 and a multi-day range, only 2 requests are made."""
        config = _make_config(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            airnow_max_hours=2,
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("airguard_data.http.HttpClient.get", return_value=mock_resp) as m:
            result = AirNowAdapter().probe(config)
        assert m.call_count == 2
        assert result.metadata["hours_requested"] == 2
        assert result.metadata["hours_capped"] is True

    def test_malformed_row_counted_not_crash(self):
        config = _make_config()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = (
            "01/01/24|00:00|123|Hanoi DOS|-7|PM2.5|UG/M3|12.3|US DOS\n"
            "MALFORMED_LINE_NO_PIPES\n"
            "01/01/24|00:00\n"  # too few fields
        )
        mock_resp.headers = {}
        with patch("airguard_data.http.HttpClient.get", return_value=mock_resp):
            result = AirNowAdapter().probe(config)
        assert result.metadata["rows_malformed"] >= 2

    def test_unrecognized_unit_rejected_counted(self):
        config = _make_config()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "01/01/24|00:00|123|Hanoi DOS|-7|PM2.5|PPM|12.3|US DOS\n"
        mock_resp.headers = {}
        with patch("airguard_data.http.HttpClient.get", return_value=mock_resp):
            result = AirNowAdapter().probe(config)
        assert result.observations.empty
        assert result.metadata["rows_unrecognized_unit"] == 1

    def test_status_always_ineligible(self):
        """Capped sample must remain ineligible regardless of content."""
        config = _make_config()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "01/01/24|00:00|123|Hanoi DOS|-7|PM2.5|UG/M3|12.3|US DOS\n"
        mock_resp.headers = {}
        with patch("airguard_data.http.HttpClient.get", return_value=mock_resp):
            result = AirNowAdapter().probe(config)
        assert result.status == SourceStatus.INELIGIBLE

    def test_all_unreachable_hours_are_blocked_network(self):
        """A transport failure is not evidence that the archive has no data."""
        config = _make_config()
        with patch(
            "airguard_data.http.HttpClient.get",
            side_effect=RedactedException("Connection error to https://example.test"),
        ):
            result = AirNowAdapter().probe(config)

        assert result.status == SourceStatus.BLOCKED_NETWORK
        assert result.metadata["hours_network_error"] == 1

    def test_metadata_reports_sample_statistics(self):
        config = _make_config()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "01/01/24|00:00|123|Hanoi DOS|-7|PM2.5|UG/M3|12.3|US DOS\n"
        mock_resp.headers = {}
        with patch("airguard_data.http.HttpClient.get", return_value=mock_resp):
            result = AirNowAdapter().probe(config)
        meta = result.metadata
        assert "hours_requested" in meta
        assert "hours_returned" in meta
        assert "hours_missing_archive" in meta
        assert "rows_parsed" in meta
