import json
import os
from pathlib import Path

import pandas as pd

def merge_pm25_weather(pm25_path: str, weather_path: str, output_path: str, report_dir: str) -> None:
    pm25_df = pd.read_csv(pm25_path)
    weather_df = pd.read_csv(weather_path)

    # Contract columns check
    weather_cols_ordered = [
        "temperature_2m_c",
        "relative_humidity_2m_pct",
        "dew_point_2m_c",
        "precipitation_mm",
        "surface_pressure_hpa",
        "cloud_cover_pct",
        "wind_speed_10m_ms",
        "wind_direction_10m_deg"
    ]

    for col in ["timestamp_utc"] + weather_cols_ordered:
        if col not in weather_df.columns:
            raise ValueError(f"Weather input is missing required column: {col}")
    if "timestamp_utc" not in pm25_df.columns:
        raise ValueError("PM2.5 input is missing required column: timestamp_utc")

    # Explicitly parse UTC
    try:
        pm25_df["timestamp_utc_parsed"] = pd.to_datetime(pm25_df["timestamp_utc"], utc=True, format="ISO8601")
    except Exception:
        pm25_df["timestamp_utc_parsed"] = pd.to_datetime(pm25_df["timestamp_utc"], utc=True, errors="coerce")
    if pm25_df["timestamp_utc_parsed"].isnull().any():
        raise ValueError("Invalid/unparseable timestamps found in PM2.5 data")

    try:
        weather_df["timestamp_utc_parsed"] = pd.to_datetime(weather_df["timestamp_utc"], utc=True, format="ISO8601")
    except Exception:
        weather_df["timestamp_utc_parsed"] = pd.to_datetime(weather_df["timestamp_utc"], utc=True, errors="coerce")
    if weather_df["timestamp_utc_parsed"].isnull().any():
        raise ValueError("Invalid/unparseable timestamps found in weather data")

    # Reject duplicate weather timestamps
    if weather_df["timestamp_utc_parsed"].duplicated().any():
        raise ValueError("Duplicate timestamps found in weather data.")

    # Standardize both to ISO string to merge and preserve PM2.5 row order
    pm25_df["timestamp_utc"] = pm25_df["timestamp_utc_parsed"].dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    weather_df["timestamp_utc"] = weather_df["timestamp_utc_parsed"].dt.strftime('%Y-%m-%dT%H:%M:%SZ')

    pm25_df = pm25_df.drop(columns=["timestamp_utc_parsed"])
    weather_df = weather_df.drop(columns=["timestamp_utc_parsed"])

    pm25_original_cols = list(pm25_df.columns)

    weather_df = weather_df[["timestamp_utc"] + weather_cols_ordered]

    # Store weather keys for availability check (key membership)
    weather_timestamps = set(weather_df["timestamp_utc"])

    # Left merge
    merged_df = pd.merge(pm25_df, weather_df, on="timestamp_utc", how="left", validate="m:1")

    # Weather available boolean
    merged_df["weather_available"] = merged_df["timestamp_utc"].isin(weather_timestamps)

    # Final column order
    final_cols = pm25_original_cols + weather_cols_ordered + ["weather_available"]
    merged_df = merged_df[final_cols]

    # Write output
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    merged_df.to_csv(out_p, index=False)

    # Write reports
    pm25_row_count = len(pm25_df)
    joined_row_count = len(merged_df)

    if "is_valid" in merged_df.columns:
        valid_mask = merged_df["is_valid"] == True
    else:
        valid_mask = pd.Series(True, index=merged_df.index)

    valid_pm25 = merged_df[valid_mask]
    weather_coverage = valid_pm25["weather_available"].sum()
    weather_coverage_ratio = float(weather_coverage / len(valid_pm25)) if len(valid_pm25) > 0 else 0.0

    null_counts = merged_df[weather_cols_ordered].isnull().sum().to_dict()
    unmatched_count = int((~merged_df["weather_available"]).sum())

    # Range reporting (ensure all are present, even if min/max are NaN)
    mins = merged_df[weather_cols_ordered].min(numeric_only=True).to_dict()
    maxs = merged_df[weather_cols_ordered].max(numeric_only=True).to_dict()

    ranges = {}
    for col in weather_cols_ordered:
        mn = mins.get(col)
        mx = maxs.get(col)
        ranges[col] = {
            "min": mn if pd.notna(mn) else None,
            "max": mx if pd.notna(mx) else None
        }

    report_data = {
        "pm25_row_count": pm25_row_count,
        "joined_row_count": joined_row_count,
        "weather_coverage_among_valid": weather_coverage_ratio,
        "unmatched_timestamp_count": unmatched_count,
        "weather_null_counts": null_counts,
        "ranges": ranges
    }

    rep_dir = Path(report_dir)
    rep_dir.mkdir(parents=True, exist_ok=True)

    with open(rep_dir / "merge_report.json", "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2, default=str)

    md = "# PM2.5 and Weather Merge Report\n\n"
    md += f"- **PM2.5 Rows**: {pm25_row_count}\n"
    md += f"- **Joined Rows**: {joined_row_count}\n"
    md += f"- **Weather Coverage (Valid PM2.5)**: {weather_coverage_ratio:.2%}\n"
    md += f"- **Unmatched Timestamps**: {unmatched_count}\n\n"
    md += "### Null Counts\n"
    for k, v in null_counts.items():
        md += f"- {k}: {v}\n"

    with open(rep_dir / "merge_report.md", "w", encoding="utf-8") as f:
        f.write(md)
