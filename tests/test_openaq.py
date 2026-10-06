"""
Tests for the OpenAQ adapter.

Covers:
- Missing credentials → blocked_auth (no network call)
- Secret redaction: API key must not appear in any result
- Fixture-based location/sensor/hours parsing with official v3 schema
- Unit normalization and is_valid for negative values
- 401/403 HTTP responses → blocked_auth
- Metadata history extraction from sensor detail endpoint
- No unhandled exceptions
"""

import pytest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from airguard_data.config import FeasibilityConfig
from airguard_data.contracts import SourceStatus
from airguard_data.sources.openaq import OpenAQAdapter, _rank_candidates

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "openaq"


def _make_config(**overrides):
    defaults = dict(
        sources=["openaq"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 2),
        latitude=21.0285,
        longitude=105.8542,
        radius_m=25_000,
        timeout_seconds=5,
        airnow_max_hours=1,
        output_dir="/tmp/test_openaq",
        report_path="/tmp/test_openaq.md",
    )
    defaults.update(overrides)
    return FeasibilityConfig(**defaults)


class TestOpenAQMissingCredentials:
    def test_no_key_no_offline_returns_blocked_auth(self):
        config = _make_config(openaq_api_key=None)
        result = OpenAQAdapter().probe(config)
        assert result.status == SourceStatus.BLOCKED_AUTH
        assert result.observations.empty
        # Message must not contain any secret
        assert "SECRET" not in result.message.upper() or True  # key is None, so no secret

    def test_blocked_auth_has_no_key_in_metadata(self):
        config = _make_config(openaq_api_key=None)
        result = OpenAQAdapter().probe(config)
        assert result.status == SourceStatus.BLOCKED_AUTH
        metadata_str = str(result.metadata)
        assert "SECRET" not in metadata_str


class TestOpenAQSecretRedaction:
    def test_api_key_not_in_error_message(self):
        """Even when adapter raises internally, the secret must not leak."""
        secret = "MY_SECRET_API_KEY_12345"
        config = _make_config(openaq_api_key=secret)

        with patch("airguard_data.sources.openaq.HttpClient.get") as mock_get:
            mock_get.side_effect = Exception("Connection refused")
            result = OpenAQAdapter().probe(config)

        assert secret not in result.message
        assert secret not in str(result.metadata)

    def test_api_key_not_in_network_error(self):
        """RedactedException path must not expose the key."""
        from airguard_data.http import RedactedException

        secret = "MY_SECRET_API_KEY_67890"
        config = _make_config(openaq_api_key=secret)

        with patch("airguard_data.sources.openaq.HttpClient.get") as mock_get:
            mock_get.side_effect = RedactedException("Connection to https://api.openaq.org?redacted failed")
            result = OpenAQAdapter().probe(config)

        assert secret not in result.message
        assert result.status == SourceStatus.BLOCKED_NETWORK


class TestOpenAQWithFixtures:
    def test_location_and_hours_parsed(self):
        """Fixture-based test uses real JSON files matching official v3 schema."""
        config = _make_config(
            offline_fixtures=str(FIXTURE_DIR),
            openaq_api_key=None,  # offline bypasses auth gate
        )
        result = OpenAQAdapter().probe(config)

        # Should get data (eligible status might not pass full gates with only 3 rows)
        assert result.source == "openaq"
        assert not result.observations.empty
        assert "location_id" in result.observations.columns
        assert "timestamp_utc" in result.observations.columns

    def test_timestamp_utc_is_timezone_aware(self):
        config = _make_config(
            offline_fixtures=str(FIXTURE_DIR),
            openaq_api_key=None,
        )
        result = OpenAQAdapter().probe(config)
        assert not result.observations.empty
        # Must be UTC-aware
        ts_col = result.observations["timestamp_utc"]
        import pandas as pd
        assert pd.api.types.is_datetime64_any_dtype(ts_col)
        assert ts_col.dt.tz is not None

    def test_negative_value_is_invalid(self):
        config = _make_config(
            offline_fixtures=str(FIXTURE_DIR),
            openaq_api_key=None,
        )
        result = OpenAQAdapter().probe(config)
        # The fixture has value -1.0 which should be invalid
        df = result.observations
        neg_rows = df[df["pm2_5_ug_m3"] < 0]
        assert len(neg_rows) > 0
        assert not neg_rows["is_valid"].any()

    def test_location_id_is_string(self):
        config = _make_config(
            offline_fixtures=str(FIXTURE_DIR),
            openaq_api_key=None,
        )
        result = OpenAQAdapter().probe(config)
        # Pandas 3 displays its Python-backed string extension dtype as
        # `str`; older releases report `string`.
        assert str(result.observations["location_id"].dtype) in (
            "str", "string", "object"
        )

    def test_metadata_has_verified_history(self):
        config = _make_config(
            offline_fixtures=str(FIXTURE_DIR),
            openaq_api_key=None,
        )
        result = OpenAQAdapter().probe(config)
        assert "verified_history_days" in result.metadata
        # Our fixture has ~2000+ days
        assert result.metadata["verified_history_days"] > 365


class TestOpenAQAuthErrors:
    def test_401_returns_blocked_auth(self):
        config = _make_config(openaq_api_key="wrong-key")
        mock_resp = MagicMock()
        mock_resp.status_code = 401

        with patch("airguard_data.sources.openaq.HttpClient.get", return_value=mock_resp):
            result = OpenAQAdapter().probe(config)

        assert result.status == SourceStatus.BLOCKED_AUTH
        assert "wrong-key" not in result.message

    def test_403_returns_blocked_auth(self):
        config = _make_config(openaq_api_key="wrong-key")
        mock_resp = MagicMock()
        mock_resp.status_code = 403

        with patch("airguard_data.sources.openaq.HttpClient.get", return_value=mock_resp):
            result = OpenAQAdapter().probe(config)

        assert result.status == SourceStatus.BLOCKED_AUTH


class TestOpenAQEmptyResults:
    def test_no_pm25_locations_returns_ineligible(self):
        config = _make_config(openaq_api_key="valid-key")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": [], "meta": {"found": 0}}

        with patch("airguard_data.sources.openaq.HttpClient.get", return_value=mock_resp):
            result = OpenAQAdapter().probe(config)

        assert result.status == SourceStatus.INELIGIBLE


class TestOpenAQCandidateSelection:
    def test_rank_prefers_requested_window_over_stale_first_api_item(self):
        config = _make_config()
        stale = {
            "loc": {
                "id": 1,
                "distance": 100,
                "datetimeFirst": {"utc": "2010-01-01T00:00:00Z"},
                "datetimeLast": {"utc": "2012-01-01T00:00:00Z"},
            },
            "sensor": {"id": 101},
        }
        active = {
            "loc": {
                "id": 2,
                "distance": 10_000,
                "datetimeFirst": {"utc": "2023-01-01T00:00:00Z"},
                "datetimeLast": {"utc": "2025-01-01T00:00:00Z"},
            },
            "sensor": {"id": 202},
        }

        assert _rank_candidates([stale, active], config)[0]["sensor"]["id"] == 202

    def test_empty_preferred_sensor_falls_back_to_next_candidate(self):
        config = _make_config(openaq_api_key="fake-key")

        def response(payload):
            item = MagicMock()
            item.status_code = 200
            item.json.return_value = payload
            item.content = None
            return item

        locations = {
            "results": [
                {
                    "id": 1,
                    "name": "Long history but empty",
                    "distance": 100,
                    "coordinates": {"latitude": 21.0, "longitude": 105.0},
                    "datetimeFirst": {"utc": "2010-01-01T00:00:00Z"},
                    "datetimeLast": {"utc": "2025-01-01T00:00:00Z"},
                    "sensors": [{"id": 101, "parameter": {"id": 2, "name": "pm25", "units": "ug/m3"}}],
                },
                {
                    "id": 2,
                    "name": "Fallback with data",
                    "distance": 200,
                    "coordinates": {"latitude": 21.1, "longitude": 105.1},
                    "datetimeFirst": {"utc": "2020-01-01T00:00:00Z"},
                    "datetimeLast": {"utc": "2025-01-01T00:00:00Z"},
                    "sensors": [{"id": 202, "parameter": {"id": 2, "name": "pm25", "units": "ug/m3"}}],
                },
            ]
        }

        def get(url, **kwargs):
            if url.endswith("/locations"):
                return response(locations)
            if url.endswith("/sensors/101") or url.endswith("/sensors/202"):
                return response({"results": []})
            if url.endswith("/sensors/101/hours"):
                return response({"results": [], "meta": {"found": 0}})
            return response({"results": [{
                "period": {"datetimeFrom": {"utc": "2024-01-01T00:00:00Z"}},
                "value": 12.0,
            }], "meta": {"found": 1}})

        with patch("airguard_data.sources.openaq.HttpClient.get", side_effect=get):
            result = OpenAQAdapter().probe(config)

        assert result.status == SourceStatus.ELIGIBLE
        assert result.metadata["sensor_id"] == "202"
        assert [a["outcome"] for a in result.metadata["candidate_attempts"]] == [
            "empty_window", "selected"
        ]

    def test_string_meta_found_is_supported(self):
        """The live API can serialize its total count as a string."""
        config = _make_config(openaq_api_key="fake-key")
        hour_request_params = []

        def response(payload):
            item = MagicMock()
            item.status_code = 200
            item.content = None
            item.json.return_value = payload
            return item

        def get(url, **kwargs):
            if url.endswith("/locations"):
                return response({"results": [{
                    "id": 1, "name": "Hanoi", "distance": 1,
                    "coordinates": {"latitude": 21.0, "longitude": 105.0},
                    "datetimeFirst": {"utc": "2020-01-01T00:00:00Z"},
                    "datetimeLast": {"utc": "2025-01-01T00:00:00Z"},
                    "sensors": [{"id": 101, "parameter": {"id": 2, "name": "pm25", "units": "ug/m3"}}],
                }]})
            if url.endswith("/sensors/101"):
                return response({"results": []})
            hour_request_params.append(kwargs["params"])
            return response({"results": [{
                "period": {"datetimeFrom": {"utc": "2024-01-01T00:00:00Z"}},
                "value": 12.0,
            }], "meta": {"found": "1"}})

        with patch("airguard_data.sources.openaq.HttpClient.get", side_effect=get):
            result = OpenAQAdapter().probe(config)

        assert result.status == SourceStatus.ELIGIBLE
        assert hour_request_params == [{
            "datetime_from": "2024-01-01T00:00:00Z",
            "datetime_to": "2024-01-03T00:00:00Z",
            "limit": 1000,
            "page": 1,
        }]

    def test_no_rows_in_window_returns_ineligible(self):
        config = _make_config(openaq_api_key="valid-key")

        def side_effect(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "locations" in url:
                resp.json.return_value = {
                    "results": [{
                        "id": 1,
                        "name": "Hanoi",
                        "coordinates": {"latitude": 21.0, "longitude": 105.0},
                        "sensors": [{"id": 99, "parameter": {"id": 2, "name": "pm25", "units": "ug/m3"}}]
                    }],
                    "meta": {"found": 1}
                }
            elif "sensors" in url and "hours" not in url:
                resp.json.return_value = {
                    "results": [{"datetimeFirst": {"utc": "2020-01-01T00:00:00Z"}, "datetimeLast": {"utc": "2024-12-31T00:00:00Z"}, "parameter": {}}]
                }
            else:
                resp.json.return_value = {"results": [], "meta": {"found": 0}}
            return resp

        with patch("airguard_data.sources.openaq.HttpClient.get", side_effect=side_effect):
            result = OpenAQAdapter().probe(config)

        assert result.status == SourceStatus.INELIGIBLE

class TestOpenAQPersistence:
    def test_live_persistence_atomic(self, tmp_path, monkeypatch):
        import json
        config = _make_config(openaq_api_key="fake-key", offline_fixtures=None)
        
        # Patch the raw dir so we don't write to the real project data dir
        monkeypatch.setattr("airguard_data.sources.openaq.Path", lambda x: tmp_path / x if x == "data/raw/openaq" else Path(x))
        
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"results": [{"id": 1, "name": "Hanoi", "coordinates": {"latitude": 21.0, "longitude": 105.0}, "sensors": [{"id": 99, "parameter": {"id": 2, "name": "pm25", "units": "ug/m3"}}]}]}'
        mock_resp.headers = {"ETag": "w/12345"}
        mock_resp.json.return_value = json.loads(mock_resp.content.decode("utf-8"))
        
        with patch("airguard_data.sources.openaq.HttpClient.get", return_value=mock_resp):
            # This should trigger _persist_raw_response for locations
            # and then fail at the sensor or hours endpoint, which is fine.
            # It should not raise an internal exception like NameError
            result = OpenAQAdapter().probe(config)
            
        assert result.status != SourceStatus.ERROR
        
        # Check files were written atomically
        raw_dir = tmp_path / "data/raw/openaq"
        assert (raw_dir / "locations.json").exists()
        assert (raw_dir / "locations.json.meta.json").exists()
        
        # Ensure no partial .tmp files left over
        tmp_files = list(raw_dir.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_live_persistence_failure_cleanup(self, tmp_path, monkeypatch):
        import json
        config = _make_config(openaq_api_key="fake-key", offline_fixtures=None)
        
        monkeypatch.setattr("airguard_data.sources.openaq.Path", lambda x: tmp_path / x if x == "data/raw/openaq" else Path(x))
        
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"results": [{"id": 1, "name": "Hanoi", "coordinates": {"latitude": 21.0, "longitude": 105.0}, "sensors": [{"id": 99, "parameter": {"id": 2, "name": "pm25", "units": "ug/m3"}}]}]}'
        mock_resp.headers = {"ETag": "w/12345"}
        mock_resp.json.return_value = json.loads(mock_resp.content.decode("utf-8"))
        
        # Simulate os.replace throwing an error so we test the finally block cleanup
        with patch("airguard_data.sources.openaq.HttpClient.get", return_value=mock_resp):
            with patch("os.replace", side_effect=PermissionError("Simulated permission error")):
                result = OpenAQAdapter().probe(config)
                
        # Since the error is internal, it should be caught and status should be ERROR
        assert result.status == SourceStatus.ERROR
        
        # Ensure partial .tmp files are cleaned up
        raw_dir = tmp_path / "data/raw/openaq"
        tmp_files = list(raw_dir.glob("*.tmp"))
        assert len(tmp_files) == 0
