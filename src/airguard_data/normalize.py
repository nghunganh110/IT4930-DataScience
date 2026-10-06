"""
Canonical normalization for PM2.5 observations.

Contract:
- Every output row has exactly the 11 canonical columns in order.
- location_id is always a str.
- timestamp_utc is a timezone-aware UTC datetime (pandas DatetimeTZDtype UTC).
- timestamp_local is a timezone-aware datetime with the record's local offset.
- pm2_5_ug_m3 is float64 (may be NaN for unparseable values).
- is_valid is bool: False when pm2_5_ug_m3 < 0 or when it is NaN.
- Rows with unrecognized units are EXCLUDED from canonical output; count is
  returned as second element of apply_canonical_rules() result.
- Exact (source, location_id, timestamp_utc) duplicates are NOT removed here;
  de-duplication happens after profiling, inside profile_series().
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from typing import Tuple

CANONICAL_COLUMNS = [
    "source",
    "provider",
    "location_id",
    "location_name",
    "latitude",
    "longitude",
    "timestamp_utc",
    "timestamp_local",
    "pm2_5_ug_m3",
    "unit_original",
    "is_valid",
]

# Recognized PM2.5 unit strings (case-insensitive lookup)
_VALID_UNITS_LOWER = {"ug/m3", "µg/m³", "ug/m³", "μg/m3", "μg/m³"}
_UNIT_NORMALISE = {
    "µg/m³": "ug/m3",
    "ug/m³": "ug/m3",
    "μg/m3": "ug/m3",
    "μg/m³": "ug/m3",
    "UG/M3": "ug/m3",
}


def _is_recognized_unit(unit: str) -> bool:
    return unit.strip().lower() in _VALID_UNITS_LOWER


def create_empty_canonical_df() -> pd.DataFrame:
    """Return an empty DataFrame with canonical column types."""
    df = pd.DataFrame(columns=CANONICAL_COLUMNS)
    for col in ("source", "provider", "location_id", "location_name", "unit_original"):
        df[col] = df[col].astype("string")
    for col in ("latitude", "longitude", "pm2_5_ug_m3"):
        df[col] = df[col].astype("float64")
    df["timestamp_utc"] = pd.Series(dtype="datetime64[ns, UTC]")
    df["timestamp_local"] = pd.Series(dtype="object")
    df["is_valid"] = pd.Series(dtype="bool")
    return df


def apply_canonical_rules(df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Normalize a raw observations DataFrame into the canonical contract.

    Returns:
        (canonical_df, rejected_unit_count)
        canonical_df  – normalized rows with recognized units
        rejected_unit_count – number of rows dropped due to unrecognized units
    """
    if df.empty:
        return create_empty_canonical_df(), 0

    df = df.copy()

    # ── 1. location_id → str ──────────────────────────────────────────────
    df["location_id"] = df["location_id"].astype(str)

    # ── 2. pm2_5_ug_m3 → numeric, coerce malformed to NaN ────────────────
    df["pm2_5_ug_m3"] = pd.to_numeric(df["pm2_5_ug_m3"], errors="coerce")

    # ── 3. is_valid: False for negative or NaN ────────────────────────────
    if "is_valid" not in df.columns:
        df["is_valid"] = True
    df["is_valid"] = df["is_valid"].astype(bool)
    mask_negative = df["pm2_5_ug_m3"] < 0
    mask_null = df["pm2_5_ug_m3"].isna()
    df.loc[mask_negative | mask_null, "is_valid"] = False

    # ── 4. Unit filter ────────────────────────────────────────────────────
    before_unit_filter = len(df)
    recognized_mask = df["unit_original"].apply(
        lambda u: _is_recognized_unit(str(u)) if pd.notna(u) else False
    )
    rejected_unit_count = int((~recognized_mask).sum())
    df = df[recognized_mask].copy()

    # ── 5. Normalise unit spelling ─────────────────────────────────────────
    df["unit_original"] = df["unit_original"].apply(
        lambda u: _UNIT_NORMALISE.get(u.strip(), u.strip().lower()) if pd.notna(u) else u
    )

    # ── 6. timestamp_utc → timezone-aware UTC ────────────────────────────
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True, errors="coerce")

    # ── 7. timestamp_local → timezone-aware if possible ───────────────────
    # Keep as-is if already aware; try parsing strings.
    def _coerce_local(val):
        if pd.isna(val):
            return val
        if isinstance(val, pd.Timestamp) and val.tzinfo is not None:
            return val
        try:
            parsed = pd.to_datetime(val)
            return parsed
        except Exception:
            return val

    df["timestamp_local"] = df["timestamp_local"].apply(_coerce_local)

    # ── 8. Sort deterministically ─────────────────────────────────────────
    df = df.sort_values(["source", "location_id", "timestamp_utc"])

    # ── 9. Ensure all canonical columns exist ─────────────────────────────
    for col in CANONICAL_COLUMNS:
        if col not in df.columns:
            df[col] = None

    return df[CANONICAL_COLUMNS].reset_index(drop=True), rejected_unit_count
