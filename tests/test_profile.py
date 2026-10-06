"""
Tests for quality profiling (profile_series).

Covers:
- Coverage ratio calculation (based on valid non-null PM2.5 hours)
- longest_consecutive_missing_hours correctly counts absent slots (not gap hours)
- Deduplication: exact (source, location_id, timestamp_utc) duplicates removed, counted
- Gate boundary values: 364/365 verified days, 69.9%/70% coverage, 999/1000 valid rows
- modeled source rejected by station_observation gate
- known_timezone: requires timezone-aware UTC timestamps
- recognized_units: requires all units == "ug/m3"
- hourly_or_normalizable: derived from data
- Empty df returns eligible=False
"""

import pytest
import pandas as pd
from datetime import datetime, timezone, timedelta

from airguard_data.profile import profile_series, _longest_consecutive_missing_hours


def _ts(h: int) -> datetime:
    return datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=h)


def _build_df(n_rows: int, valid: bool = True, units: str = "ug/m3",
              tz_aware: bool = True, gaps: list[int] | None = None) -> pd.DataFrame:
    """
    Build a canonical-schema DataFrame with n_rows consecutive hourly rows.
    If gaps is provided, those hour indices are omitted.
    """
    gap_set = set(gaps or [])
    rows = []
    for i in range(n_rows):
        if i in gap_set:
            continue
        ts = _ts(i)
        rows.append({
            "source": "test_src",
            "location_id": "1",
            "timestamp_utc": ts if tz_aware else ts.replace(tzinfo=None),
            "timestamp_local": ts,
            "pm2_5_ug_m3": 15.0 if valid else -1.0,
            "unit_original": units,
            "is_valid": valid,
        })
    return pd.DataFrame(rows)


class TestProfileLongestMissingHours:
    def test_no_gap(self):
        ts = pd.Series([_ts(0), _ts(1), _ts(2)])
        assert _longest_consecutive_missing_hours(ts) == 0

    def test_one_missing_hour(self):
        # gap: 00→02 (01 missing = 1 slot)
        ts = pd.Series([_ts(0), _ts(2)])
        assert _longest_consecutive_missing_hours(ts) == 1

    def test_two_missing_hours(self):
        # 00→03 means 01 and 02 missing = 2 consecutive slots
        ts = pd.Series([_ts(0), _ts(3)])
        assert _longest_consecutive_missing_hours(ts) == 2

    def test_multiple_gaps_returns_max(self):
        # Gap 1: 00→02 = 1 missing; Gap 2: 03→07 = 3 missing
        ts = pd.Series([_ts(0), _ts(2), _ts(3), _ts(7)])
        result = _longest_consecutive_missing_hours(ts)
        assert result == 3

    def test_single_row(self):
        ts = pd.Series([_ts(0)])
        assert _longest_consecutive_missing_hours(ts) == 0


class TestProfileCoverage:
    def test_full_coverage(self):
        df = _build_df(100)
        meta = {"verified_history_days": 400.0, "is_modeled": False}
        prof, _ = profile_series(df, meta)
        assert prof["coverage_ratio"] == pytest.approx(1.0)
        assert prof["missing_hour_count"] == 0

    def test_partial_coverage(self):
        # 50 valid hours and 50 invalid-but-observed hours spanning 100
        # hours.  Invalid measurements must not contribute to coverage.
        df = _build_df(100)
        df.loc[df.index >= 50, "pm2_5_ug_m3"] = -1.0
        df.loc[df.index >= 50, "is_valid"] = False
        meta = {"verified_history_days": 400.0, "is_modeled": False}
        prof, _ = profile_series(df, meta)
        assert prof["coverage_ratio"] == pytest.approx(50 / 100)

    def test_invalid_rows_excluded_from_coverage(self):
        """Invalid (negative) rows should not count toward valid hours."""
        valid_rows = [{"source": "s", "location_id": "1", "timestamp_utc": _ts(i),
                       "timestamp_local": _ts(i), "pm2_5_ug_m3": 15.0,
                       "unit_original": "ug/m3", "is_valid": True}
                      for i in range(80)]
        invalid_rows = [{"source": "s", "location_id": "1", "timestamp_utc": _ts(i),
                         "timestamp_local": _ts(i), "pm2_5_ug_m3": -5.0,
                         "unit_original": "ug/m3", "is_valid": False}
                        for i in range(80, 100)]
        df = pd.DataFrame(valid_rows + invalid_rows)
        meta = {"verified_history_days": 400.0, "is_modeled": False}
        prof, _ = profile_series(df, meta)
        # 80 valid hours out of 100 expected
        assert prof["coverage_ratio"] == pytest.approx(0.80)


class TestProfileDeduplication:
    def test_exact_duplicates_counted(self):
        ts = _ts(0)
        rows = [
            {"source": "s", "location_id": "1", "timestamp_utc": ts, "timestamp_local": ts,
             "pm2_5_ug_m3": 10.0, "unit_original": "ug/m3", "is_valid": True},
            {"source": "s", "location_id": "1", "timestamp_utc": ts, "timestamp_local": ts,
             "pm2_5_ug_m3": 10.0, "unit_original": "ug/m3", "is_valid": True},
            {"source": "s", "location_id": "1", "timestamp_utc": _ts(1), "timestamp_local": _ts(1),
             "pm2_5_ug_m3": 15.0, "unit_original": "ug/m3", "is_valid": True},
        ]
        df = pd.DataFrame(rows)
        meta = {"verified_history_days": 1.0, "is_modeled": False}
        prof, dedup = profile_series(df, meta)
        assert prof["row_count_before"] == 3
        assert prof["duplicate_count"] == 1
        assert prof["row_count_after"] == 2
        assert len(dedup) == 2


class TestGateBoundaries:
    def test_364_days_fails_365_gate(self):
        df = _build_df(100)
        meta = {"verified_history_days": 364.9, "is_modeled": False}
        prof, _ = profile_series(df, meta)
        assert prof["gates"]["365_days_history"] is False

    def test_365_days_passes_gate(self):
        df = _build_df(100)
        meta = {"verified_history_days": 365.0, "is_modeled": False}
        prof, _ = profile_series(df, meta)
        assert prof["gates"]["365_days_history"] is True

    def test_699_pct_coverage_fails(self):
        """Build a scenario where coverage is just below 70%."""
        # 69 valid of 100 expected = 69%
        valid_rows = [{"source": "s", "location_id": "1", "timestamp_utc": _ts(i),
                       "timestamp_local": _ts(i), "pm2_5_ug_m3": 15.0,
                       "unit_original": "ug/m3", "is_valid": True}
                      for i in range(69)]
        null_rows = [{"source": "s", "location_id": "1", "timestamp_utc": _ts(i),
                      "timestamp_local": _ts(i), "pm2_5_ug_m3": None,
                      "unit_original": "ug/m3", "is_valid": False}
                     for i in range(69, 100)]
        df = pd.DataFrame(valid_rows + null_rows)
        meta = {"verified_history_days": 400.0, "is_modeled": False}
        prof, _ = profile_series(df, meta)
        assert prof["gates"]["70_percent_coverage"] is False

    def test_70_pct_coverage_passes(self):
        """Build a scenario where coverage is exactly 70%."""
        valid_rows = [{"source": "s", "location_id": "1", "timestamp_utc": _ts(i),
                       "timestamp_local": _ts(i), "pm2_5_ug_m3": 15.0,
                       "unit_original": "ug/m3", "is_valid": True}
                      for i in range(70)]
        null_rows = [{"source": "s", "location_id": "1", "timestamp_utc": _ts(i),
                      "timestamp_local": _ts(i), "pm2_5_ug_m3": None,
                      "unit_original": "ug/m3", "is_valid": False}
                     for i in range(70, 100)]
        df = pd.DataFrame(valid_rows + null_rows)
        meta = {"verified_history_days": 400.0, "is_modeled": False}
        prof, _ = profile_series(df, meta)
        assert prof["gates"]["70_percent_coverage"] is True

    def test_999_valid_rows_fails_1000_gate(self):
        df = _build_df(999)
        meta = {"verified_history_days": 400.0, "is_modeled": False}
        prof, _ = profile_series(df, meta)
        assert prof["gates"]["1000_valid_rows"] is False

    def test_1000_valid_rows_passes_gate(self):
        df = _build_df(1000)
        meta = {"verified_history_days": 400.0, "is_modeled": False}
        prof, _ = profile_series(df, meta)
        assert prof["gates"]["1000_valid_rows"] is True

    def test_modeled_source_fails_station_observation(self):
        df = _build_df(1000)
        meta = {"verified_history_days": 400.0, "is_modeled": True}
        prof, _ = profile_series(df, meta)
        assert prof["gates"]["station_observation"] is False
        assert prof["eligible"] is False

    def test_known_timezone_requires_utc_aware(self):
        """Without timezone-aware timestamps, known_timezone must be False."""
        df = _build_df(100, tz_aware=False)
        meta = {"verified_history_days": 400.0, "is_modeled": False}
        prof, _ = profile_series(df, meta)
        assert prof["gates"]["known_timezone"] is False

    def test_unrecognized_unit_fails_recognized_units_gate(self):
        df = _build_df(1000, units="ppm")
        meta = {"verified_history_days": 400.0, "is_modeled": False}
        prof, _ = profile_series(df, meta)
        assert prof["gates"]["recognized_units"] is False

    def test_empty_df_not_eligible(self):
        df = pd.DataFrame()
        meta = {"verified_history_days": 400.0, "is_modeled": False}
        prof, _ = profile_series(df, meta)
        assert prof["eligible"] is False


class TestProfileStats:
    def test_stats_computed_for_valid_rows(self):
        df = _build_df(10)
        df["pm2_5_ug_m3"] = [10.0, 20.0, 15.0, 5.0, 25.0, 30.0, 8.0, 12.0, 18.0, 22.0]
        meta = {"verified_history_days": 400.0, "is_modeled": False}
        prof, _ = profile_series(df, meta)
        stats = prof["stats"]
        assert stats["min"] == pytest.approx(5.0)
        assert stats["max"] == pytest.approx(30.0)
        assert stats["mean"] == pytest.approx(16.5)
