import json
import os
from typing import List, Tuple, Dict, Any
import pandas as pd
import numpy as np

WEATHER_SCALARS = [
    "temperature_2m_c",
    "relative_humidity_2m_pct",
    "dew_point_2m_c",
    "precipitation_mm",
    "surface_pressure_hpa",
    "cloud_cover_pct",
    "wind_speed_10m_ms",
]

def parse_int_list_option(value: str) -> List[int]:
    if not value:
        return []
    try:
        parts = [int(x.strip()) for x in value.split(",")]
    except ValueError:
        raise ValueError(f"Invalid integer list: {value}")
    if any(x <= 0 for x in parts):
        raise ValueError("List values must be positive")
    
    seen = set()
    res = []
    for x in parts:
        if x not in seen:
            res.append(x)
            seen.add(x)
        else:
            raise ValueError(f"Duplicate values are not allowed: {value}")
    return res

def build_features(
    input_path: str,
    output_path: str,
    report_dir: str,
    horizons: List[int],
    lags: List[int],
    rolling_windows: List[int],
    local_timezone: str
) -> None:
    # 1. Read input
    df = pd.read_csv(input_path)
    
    required_cols = ["timestamp_utc", "pm2_5_ug_m3", "is_valid", "weather_available"] + WEATHER_SCALARS + ["wind_direction_10m_deg"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    try:
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True, format="ISO8601")
    except Exception:
        df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True, errors="coerce")

    if df["timestamp_utc"].isnull().any():
        raise ValueError("Malformed timestamps found")
    
    if df["timestamp_utc"].duplicated().any():
        raise ValueError("Duplicate timestamps found")

    df.sort_values("timestamp_utc", inplace=True)
    df.reset_index(drop=True, inplace=True)

    initial_rows = len(df)

    # 2. Filtering
    df["pm25_valid"] = df["is_valid"] & (df["pm2_5_ug_m3"] >= 0) & df["pm2_5_ug_m3"].notnull()
    
    weather_cond = df["weather_available"] == True
    for c in WEATHER_SCALARS + ["wind_direction_10m_deg"]:
        weather_cond &= df[c].notnull()
    df["weather_eligible"] = weather_cond

    valid_pm25_rows = df["pm25_valid"].sum()
    weather_eligible_rows = df["weather_eligible"].sum()

    # Apply strict eligibility rule for calculating lags/targets: Wait, plan says "All lags, rollings and future targets must stay inside this segment. Build continuity_segment_id using every missing/non-hourly PM2.5 interval".
    # This means the continuity segment depends on actual timestamps, regardless of eligibility.
    # Actually wait. "Build continuity_segment_id using every missing/non-hourly PM2.5 interval: any timestamp difference other than exactly one hour starts a new segment."
    df = df[df["pm25_valid"]].copy()
    time_diffs = df["timestamp_utc"].diff()
    new_segment = (time_diffs != pd.Timedelta(hours=1))
    df["continuity_segment_id"] = new_segment.cumsum()
    segment_count = df["continuity_segment_id"].nunique()

    def group_features(g: pd.DataFrame) -> pd.DataFrame:
        g = g.copy()
        g["pm25_current_ug_m3"] = g["pm2_5_ug_m3"]

        for lag in lags:
            g[f"pm25_lag_{lag}h"] = g["pm2_5_ug_m3"].shift(lag)
            
        for w in rolling_windows:
            rolling = g["pm2_5_ug_m3"].rolling(window=w, min_periods=w)
            g[f"pm25_roll_mean_{w}h"] = rolling.mean()
            g[f"pm25_roll_std_{w}h"] = rolling.std()
            g[f"pm25_roll_min_{w}h"] = rolling.min()
            g[f"pm25_roll_max_{w}h"] = rolling.max()

        for h in horizons:
            g[f"target_pm25_h{h}"] = g["pm2_5_ug_m3"].shift(-h)
            g[f"target_timestamp_h{h}"] = g["timestamp_utc"].shift(-h)

        return g

    if not df.empty:
        result_dfs = []
        for _, g in df.groupby("continuity_segment_id"):
            result_dfs.append(group_features(g))
        df = pd.concat(result_dfs)
    if df.empty: # if empty input initially
        df = pd.DataFrame(columns=required_cols + ["continuity_segment_id", "pm25_current_ug_m3"])

    # 4. Eligibility for issue time t
    eligible_cond = df["pm25_valid"] & df["weather_eligible"]
    
    invalid_pm25_or_weather_missing = initial_rows - int(eligible_cond.sum())
    
    for lag in lags:
        eligible_cond &= df[f"pm25_lag_{lag}h"].notnull()
        
    for w in rolling_windows:
        eligible_cond &= df[f"pm25_roll_mean_{w}h"].notnull()

    incomplete_lag_or_rolling = (initial_rows - invalid_pm25_or_weather_missing) - int(eligible_cond.sum())

    df_features = df[eligible_cond].copy()

    # 5. Local calendar features
    if df_features.empty:
        pass
    else:
        try:
            local_ts = df_features["timestamp_utc"].dt.tz_convert(local_timezone)
        except Exception as e:
            raise ValueError(f"Invalid timezone: {local_timezone}") from e

        df_features["hour"] = local_ts.dt.hour
        df_features["day_of_week"] = local_ts.dt.dayofweek
        df_features["month"] = local_ts.dt.month
        df_features["is_weekend"] = df_features["day_of_week"].isin([5, 6])
        
        df_features["hour_sin"] = np.sin(2 * np.pi * df_features["hour"] / 24.0)
        df_features["hour_cos"] = np.cos(2 * np.pi * df_features["hour"] / 24.0)
        df_features["day_of_week_sin"] = np.sin(2 * np.pi * df_features["day_of_week"] / 7.0)
        df_features["day_of_week_cos"] = np.cos(2 * np.pi * df_features["day_of_week"] / 7.0)
        df_features["month_sin"] = np.sin(2 * np.pi * (df_features["month"] - 1) / 12.0)
        df_features["month_cos"] = np.cos(2 * np.pi * (df_features["month"] - 1) / 12.0)

        # 6. Current weather processing
        df_features["wind_direction_10m_sin"] = np.sin(np.radians(df_features["wind_direction_10m_deg"]))
        df_features["wind_direction_10m_cos"] = np.cos(np.radians(df_features["wind_direction_10m_deg"]))

    # 7. Reorder columns to match output contract
    out_cols = ["timestamp_utc", "continuity_segment_id", "pm25_current_ug_m3"]
    for lag in lags:
        out_cols.append(f"pm25_lag_{lag}h")
    for w in rolling_windows:
        out_cols.extend([f"pm25_roll_mean_{w}h", f"pm25_roll_std_{w}h", f"pm25_roll_min_{w}h", f"pm25_roll_max_{w}h"])
    out_cols.extend([
        "hour", "day_of_week", "month", "is_weekend",
        "hour_sin", "hour_cos",
        "day_of_week_sin", "day_of_week_cos",
        "month_sin", "month_cos"
    ])
    out_cols.extend(WEATHER_SCALARS)
    out_cols.extend(["wind_direction_10m_sin", "wind_direction_10m_cos"])
    for h in horizons:
        out_cols.extend([f"target_pm25_h{h}", f"target_timestamp_h{h}"])

    if df_features.empty:
        df_final = pd.DataFrame(columns=out_cols)
    else:
        df_final = df_features[out_cols].copy()
        
        df_final["timestamp_utc"] = df_final["timestamp_utc"].dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        for h in horizons:
            ts_col = f"target_timestamp_h{h}"
            df_final[ts_col] = df_final[ts_col].dt.strftime('%Y-%m-%dT%H:%M:%SZ')

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    df_final.to_csv(output_path, index=False)

    feature_output_rows = len(df_final)
    if feature_output_rows > 0:
        horizon_counts = {h: int(df_final[f"target_pm25_h{h}"].notnull().sum()) for h in horizons}
    else:
        horizon_counts = {h: 0 for h in horizons}

    report = {
        "input_rows": initial_rows,
        "valid_pm25_rows": int(valid_pm25_rows),
        "weather_eligible_rows": int(weather_eligible_rows),
        "feature_output_rows": feature_output_rows,
        "continuity_segment_count": segment_count if initial_rows > 0 else 0,
        "configured_options": {
            "horizons": horizons,
            "lags": lags,
            "rolling_windows": rolling_windows,
            "local_timezone": local_timezone
        },
        "feature_column_names": out_cols,
        "per_horizon_available_targets": horizon_counts,
        "timestamp_range": {
            "start": str(df_final["timestamp_utc"].min()) if feature_output_rows > 0 else None,
            "end": str(df_final["timestamp_utc"].max()) if feature_output_rows > 0 else None
        },
        "drop_reasons": {
            "invalid_pm25_or_weather_missing": invalid_pm25_or_weather_missing,
            "incomplete_lag_or_rolling": incomplete_lag_or_rolling
        }
    }
    
    if report_dir:
        os.makedirs(report_dir, exist_ok=True)
    with open(os.path.join(report_dir, "feature_report.json"), "w") as f:
        json.dump(report, f, indent=2)

    with open(os.path.join(report_dir, "feature_report.md"), "w") as f:
        f.write("# Feature Engineering Report\n\n")
        f.write(f"- Input rows: {initial_rows}\n")
        f.write(f"- Valid PM2.5 rows: {valid_pm25_rows}\n")
        f.write(f"- Weather eligible rows: {weather_eligible_rows}\n")
        f.write(f"- Continuity segment count: {segment_count if initial_rows > 0 else 0}\n")
        f.write(f"- Feature output rows: {feature_output_rows}\n\n")
        f.write("## Per-horizon available targets\n")
        for h, count in horizon_counts.items():
            f.write(f"- Horizon {h}h: {count}\n")
        f.write("\n## Configured options\n")
        f.write(f"- Horizons: {horizons}\n")
        f.write(f"- Lags: {lags}\n")
        f.write(f"- Rolling windows: {rolling_windows}\n")
        f.write(f"- Local timezone: {local_timezone}\n")
