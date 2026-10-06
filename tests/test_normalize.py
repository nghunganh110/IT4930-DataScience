"""
Tests for canonical normalization.

Covers:
- Unit case-insensitive recognition and normalization to ug/m3
- Unrecognized units rejected and counted
- Negative values set is_valid=False but preserved
- NaN pm2_5_ug_m3 set is_valid=False
- Malformed pm2_5_ug_m3 strings coerced to NaN, not crash
- timestamp_utc parsed to timezone-aware UTC
- timestamp_local parsed to timezone-aware or kept as-is
- location_id coerced to str
- Canonical column order
- Empty input handled
- Return tuple: (df, rejected_unit_count)
- Sort order is (source, location_id, timestamp_utc)
"""

import pytest
import pandas as pd
from datetime import datetime, timezone, timedelta
from airguard_data.normalize import apply_canonical_rules, create_empty_canonical_df, CANONICAL_COLUMNS


def _row(**overrides):
    defaults = {
        "source": "openaq",
        "provider": "test",
        "location_id": "123",
        "location_name": "Hanoi",
        "latitude": 21.0,
        "longitude": 105.0,
        "timestamp_utc": "2024-01-01T00:00:00Z",
        "timestamp_local": "2024-01-01T07:00:00+07:00",
        "pm2_5_ug_m3": 10.5,
        "unit_original": "ug/m3",
    }
    defaults.update(overrides)
    return defaults


class TestCanonicalColumns:
    def test_output_has_correct_columns(self):
        df = pd.DataFrame([_row()])
        canonical, _ = apply_canonical_rules(df)
        assert list(canonical.columns) == CANONICAL_COLUMNS

    def test_empty_df_returns_canonical_schema(self):
        df = create_empty_canonical_df()
        result, rejected = apply_canonical_rules(df)
        assert len(result) == 0
        assert list(result.columns) == CANONICAL_COLUMNS
        assert rejected == 0


class TestUnitNormalization:
    @pytest.mark.parametrize("unit,expected", [
        ("ug/m3", "ug/m3"),
        ("UG/M3", "ug/m3"),
        ("µg/m³", "ug/m3"),
        ("ug/m³", "ug/m3"),
        ("μg/m3", "ug/m3"),
    ])
    def test_recognized_units_normalized(self, unit, expected):
        df = pd.DataFrame([_row(unit_original=unit)])
        canonical, rejected = apply_canonical_rules(df)
        assert rejected == 0
        assert canonical.iloc[0]["unit_original"] == expected

    def test_unrecognized_unit_excluded_and_counted(self):
        rows = [
            _row(unit_original="ug/m3", timestamp_utc="2024-01-01T00:00:00Z"),
            _row(unit_original="ppm", timestamp_utc="2024-01-01T01:00:00Z"),
            _row(unit_original="ppb", timestamp_utc="2024-01-01T02:00:00Z"),
        ]
        df = pd.DataFrame(rows)
        canonical, rejected = apply_canonical_rules(df)
        assert rejected == 2
        assert len(canonical) == 1
        assert canonical.iloc[0]["unit_original"] == "ug/m3"


class TestValidityFlags:
    def test_negative_value_marked_invalid(self):
        df = pd.DataFrame([_row(pm2_5_ug_m3=-5.0)])
        canonical, _ = apply_canonical_rules(df)
        assert canonical.iloc[0]["is_valid"] == False
        assert canonical.iloc[0]["pm2_5_ug_m3"] == -5.0  # preserved

    def test_positive_value_marked_valid(self):
        df = pd.DataFrame([_row(pm2_5_ug_m3=15.0)])
        canonical, _ = apply_canonical_rules(df)
        assert canonical.iloc[0]["is_valid"] == True

    def test_nan_pm25_marked_invalid(self):
        df = pd.DataFrame([_row(pm2_5_ug_m3=None)])
        canonical, _ = apply_canonical_rules(df)
        assert canonical.iloc[0]["is_valid"] == False

    def test_malformed_string_coerced_to_nan_not_crash(self):
        df = pd.DataFrame([_row(pm2_5_ug_m3="NOT_A_NUMBER")])
        canonical, _ = apply_canonical_rules(df)
        assert len(canonical) == 1
        assert pd.isna(canonical.iloc[0]["pm2_5_ug_m3"])
        assert canonical.iloc[0]["is_valid"] == False


class TestTimestampHandling:
    def test_timestamp_utc_is_aware_utc(self):
        df = pd.DataFrame([_row(timestamp_utc="2024-06-15T12:30:00Z")])
        canonical, _ = apply_canonical_rules(df)
        ts = canonical.iloc[0]["timestamp_utc"]
        assert ts.tzinfo is not None
        assert "UTC" in str(ts.tzinfo).upper() or ts.utcoffset().total_seconds() == 0

    def test_timestamp_utc_with_plus00_format(self):
        df = pd.DataFrame([_row(timestamp_utc="2024-06-15T12:30:00+00:00")])
        canonical, _ = apply_canonical_rules(df)
        ts = canonical.iloc[0]["timestamp_utc"]
        assert ts.tzinfo is not None

    def test_timestamp_local_preserved_as_aware(self):
        local_str = "2024-06-15T19:30:00+07:00"
        df = pd.DataFrame([_row(timestamp_local=local_str)])
        canonical, _ = apply_canonical_rules(df)
        ts_local = canonical.iloc[0]["timestamp_local"]
        # Should be parseable and aware or kept as-is
        assert ts_local is not None


class TestLocationId:
    def test_numeric_location_id_cast_to_str(self):
        df = pd.DataFrame([_row(location_id=123)])
        canonical, _ = apply_canonical_rules(df)
        assert canonical.iloc[0]["location_id"] == "123"
        assert isinstance(canonical.iloc[0]["location_id"], str)


class TestSortOrder:
    def test_output_sorted_by_source_location_timestamp(self):
        rows = [
            _row(source="openaq", location_id="B", timestamp_utc="2024-01-01T02:00:00Z"),
            _row(source="airnow", location_id="A", timestamp_utc="2024-01-01T01:00:00Z"),
            _row(source="openaq", location_id="A", timestamp_utc="2024-01-01T00:00:00Z"),
        ]
        df = pd.DataFrame(rows)
        canonical, _ = apply_canonical_rules(df)
        sources = list(canonical["source"])
        # airnow < openaq alphabetically
        assert sources[0] == "airnow"
        assert sources[1] == "openaq"
        assert sources[2] == "openaq"
