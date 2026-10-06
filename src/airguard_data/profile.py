"""
Quality profiling for canonical PM2.5 observation series.

Terminology used in this module:
  - "observed hours"   : unique hourly slots present in the deduplicated data
  - "expected hours"   : every hourly slot between first and last observed timestamp
  - "missing hours"    : expected_hours − unique_observed_hours
  - "longest_consecutive_missing_hours": the longest run of consecutive absent
    hourly slots (a two-hour gap between consecutive records means ONE absent slot)

Gate criteria (from PLAN.md):
  - station_observation  : not modeled/reanalysis
  - hourly_or_normalizable: timestamps can represent hourly cadence
  - 365_days_history     : verified_history_days >= 365
  - 70_percent_coverage  : coverage_ratio of valid non-null hours >= 0.70
  - recognized_units     : all units in deduplicated data are "ug/m3"
  - known_timezone       : timestamp_utc column is timezone-aware UTC
  - 1000_valid_rows      : valid non-null count >= 1000
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from typing import Dict, Any, Tuple


def _longest_consecutive_missing_hours(timestamps: pd.Series) -> int:
    """
    Given a sorted Series of timezone-aware hourly timestamps, compute the
    longest run of consecutive absent hourly slots.

    Example:
      [00:00, 02:00, 05:00]  →  gaps are 1 missing (01:00) and 2 missing (03:00, 04:00)
      → returns 2
    """
    if len(timestamps) < 2:
        return 0

    ts = timestamps.sort_values().dt.floor("h").drop_duplicates()
    if len(ts) < 2:
        return 0

    # Gap in hours between consecutive observed timestamps
    gaps_hours = ts.diff().iloc[1:].dt.total_seconds() / 3600.0
    # Missing slots = gap_hours - 1  (a 2-hour gap means 1 absent slot)
    missing_slots = (gaps_hours - 1).clip(lower=0).astype(int)
    return int(missing_slots.max()) if len(missing_slots) > 0 else 0


def _has_known_timezone(col: pd.Series) -> bool:
    """Return True if the column is a timezone-aware UTC DatetimeTZDtype."""
    if not pd.api.types.is_datetime64_any_dtype(col):
        return False
    try:
        return col.dt.tz is not None
    except Exception:
        return False


def profile_series(
    df: pd.DataFrame,
    source_metadata: Dict[str, Any],
) -> Tuple[Dict[str, Any], pd.DataFrame]:
    """
    Profile a canonical observations DataFrame for a single (source, location_id).

    Returns:
        (profile_dict, deduplicated_df)

    The deduplicated_df has exact (source, location_id, timestamp_utc) duplicates
    removed deterministically (first occurrence kept after sort).
    """
    if df.empty:
        return {"eligible": False, "reason": "empty"}, df

    is_modeled: bool = bool(source_metadata.get("is_modeled", False))

    # ── 1. Deduplication ─────────────────────────────────────────────────
    row_count_before = len(df)
    df_sorted = df.sort_values(["source", "location_id", "timestamp_utc"])
    df_dedup = df_sorted.drop_duplicates(
        subset=["source", "location_id", "timestamp_utc"], keep="first"
    ).copy()
    row_count_after = len(df_dedup)
    duplicate_count = row_count_before - row_count_after

    # ── 2. Timestamps ────────────────────────────────────────────────────
    first_ts = df_dedup["timestamp_utc"].min()
    last_ts = df_dedup["timestamp_utc"].max()

    first_ts_iso = first_ts.isoformat() if not pd.isna(first_ts) else None
    last_ts_iso = last_ts.isoformat() if not pd.isna(last_ts) else None

    observed_duration_days = (
        (last_ts - first_ts).total_seconds() / 86_400.0
        if not pd.isna(first_ts) and not pd.isna(last_ts)
        else 0.0
    )

    # ── 3. Expected vs observed hours ────────────────────────────────────
    if not pd.isna(first_ts) and not pd.isna(last_ts) and first_ts != last_ts:
        expected_range = pd.date_range(
            start=first_ts.floor("h"),
            end=last_ts.floor("h"),
            freq="h",
            tz="UTC",
        )
        expected_hours = len(expected_range)
    else:
        expected_range = []
        expected_hours = 1 if not pd.isna(first_ts) else 0

    # Coverage: based on valid non-null pm2_5 timestamps only
    valid_mask = df_dedup["is_valid"] & df_dedup["pm2_5_ug_m3"].notna()
    valid_df = df_dedup[valid_mask].copy()
    valid_count = len(valid_df)

    unique_valid_hours = valid_df["timestamp_utc"].dt.floor("h").nunique() if valid_count > 0 else 0
    coverage_ratio = unique_valid_hours / expected_hours if expected_hours > 0 else 0.0
    missing_hour_count = expected_hours - unique_valid_hours

    # ── 4. Longest consecutive missing-hour gap ───────────────────────────
    # An invalid/null reading does not establish usable hourly coverage, so it
    # belongs to the same missing-run calculation as coverage itself.
    longest_consecutive_missing_hours = _longest_consecutive_missing_hours(
        valid_df["timestamp_utc"]
    )

    # ── 5. Null / invalid counts ─────────────────────────────────────────
    null_pm25_count = int(df_dedup["pm2_5_ug_m3"].isna().sum())
    invalid_negative_count = int((df_dedup["pm2_5_ug_m3"] < 0).sum())

    # ── 6. Summary statistics (valid rows only) ───────────────────────────
    if valid_count > 0:
        stats = {
            "min": float(valid_df["pm2_5_ug_m3"].min()),
            "median": float(valid_df["pm2_5_ug_m3"].median()),
            "mean": float(valid_df["pm2_5_ug_m3"].mean()),
            "p95": float(valid_df["pm2_5_ug_m3"].quantile(0.95)),
            "max": float(valid_df["pm2_5_ug_m3"].max()),
        }
    else:
        stats = {"min": None, "median": None, "mean": None, "p95": None, "max": None}

    # ── 7. Units and timezone ────────────────────────────────────────────
    units = sorted(df_dedup["unit_original"].dropna().unique().tolist())
    known_timezone = _has_known_timezone(df_dedup["timestamp_utc"])

    # ── 8. Verified history (from metadata, not from downloaded window) ───
    verified_history_days: float = float(
        source_metadata.get("verified_history_days", 0.0)
    )

    # ── 9. hourly_or_normalizable: derived from timestamp cadence ─────────
    # True when we actually have ≥2 distinct hourly floor values
    hourly_or_normalizable = (df_dedup["timestamp_utc"].dt.floor("h").nunique() >= 2)

    # ── 10. recognized_units gate ─────────────────────────────────────────
    recognized_units = all(u == "ug/m3" for u in units) and len(units) > 0

    # ── 11. Gates ────────────────────────────────────────────────────────
    gates = {
        "station_observation": not is_modeled,
        "hourly_or_normalizable": hourly_or_normalizable,
        "365_days_history": verified_history_days >= 365,
        "70_percent_coverage": coverage_ratio >= 0.70,
        "recognized_units": recognized_units,
        "known_timezone": known_timezone,
        "1000_valid_rows": valid_count >= 1000,
    }
    passes_all = all(gates.values())

    profile: Dict[str, Any] = {
        "row_count_before": row_count_before,
        "row_count_after": row_count_after,
        "duplicate_count": duplicate_count,
        "first_timestamp": first_ts_iso,
        "last_timestamp": last_ts_iso,
        "observed_duration_days": observed_duration_days,
        "verified_history_days": verified_history_days,
        "expected_hours": expected_hours,
        "unique_valid_hours": unique_valid_hours,
        "coverage_ratio": coverage_ratio,
        "missing_hour_count": missing_hour_count,
        "longest_consecutive_missing_hours": longest_consecutive_missing_hours,
        "null_pm25_count": null_pm25_count,
        "invalid_negative_count": invalid_negative_count,
        "valid_count": valid_count,
        "stats": stats,
        "units": units,
        "known_timezone": known_timezone,
        "gates": gates,
        "eligible": passes_all,
    }

    return profile, df_dedup
