import json
import pytest
import pandas as pd
from pathlib import Path
from airguard_data.weather_merge import merge_pm25_weather

def create_valid_dfs():
    pm25_df = pd.DataFrame({
        "timestamp_utc": ["2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z", "2024-01-01T02:00:00Z"],
        "pm2_5_ug_m3": [10.0, 15.0, 20.0],
        "is_valid": [True, True, False]
    })
    weather_df = pd.DataFrame({
        "timestamp_utc": ["2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z"],
        "temperature_2m_c": [20.0, 21.0],
        "relative_humidity_2m_pct": [80, 81],
        "dew_point_2m_c": [15.0, 15.1],
        "precipitation_mm": [0.0, 0.0],
        "surface_pressure_hpa": [1010.0, 1010.0],
        "cloud_cover_pct": [50, 50],
        "wind_speed_10m_ms": [2.0, 2.0],
        "wind_direction_10m_deg": [90, 90]
    })
    return pm25_df, weather_df

def test_merge_pm25_weather(tmp_path):
    pm25_path = tmp_path / "pm25.csv"
    weather_path = tmp_path / "weather.csv"
    out_csv = tmp_path / "out.csv"
    rep_dir = tmp_path / "report"

    pm25_df, weather_df = create_valid_dfs()
    # Introduce a null measurement to test key-membership policy
    weather_df.loc[1, "temperature_2m_c"] = None
    
    pm25_df.to_csv(pm25_path, index=False)
    weather_df.to_csv(weather_path, index=False)

    merge_pm25_weather(str(pm25_path), str(weather_path), str(out_csv), str(rep_dir))

    out_df = pd.read_csv(out_csv)
    assert len(out_df) == 3
    assert "weather_available" in out_df.columns

    # 1. Assert row order is preserved
    assert list(out_df["timestamp_utc"]) == list(pm25_df["timestamp_utc"])
    
    # 2. Assert original PM2.5 values are preserved exactly
    assert list(out_df["pm2_5_ug_m3"]) == list(pm25_df["pm2_5_ug_m3"])

    assert out_df.loc[out_df["timestamp_utc"] == "2024-01-01T00:00:00Z", "temperature_2m_c"].iloc[0] == 20.0
    assert out_df.loc[out_df["timestamp_utc"] == "2024-01-01T00:00:00Z", "weather_available"].iloc[0] == True

    # 3. Assert matching timestamp with null measurement still produces weather_available=True
    assert pd.isna(out_df.loc[out_df["timestamp_utc"] == "2024-01-01T01:00:00Z", "temperature_2m_c"].iloc[0])
    assert out_df.loc[out_df["timestamp_utc"] == "2024-01-01T01:00:00Z", "weather_available"].iloc[0] == True

    assert pd.isna(out_df.loc[out_df["timestamp_utc"] == "2024-01-01T02:00:00Z", "temperature_2m_c"].iloc[0])
    assert out_df.loc[out_df["timestamp_utc"] == "2024-01-01T02:00:00Z", "weather_available"].iloc[0] == False

    rep_json = rep_dir / "merge_report.json"
    assert rep_json.exists()
    rep_data = json.loads(rep_json.read_text())
    assert rep_data["pm25_row_count"] == 3
    assert rep_data["joined_row_count"] == 3
    assert rep_data["unmatched_timestamp_count"] == 1

    rep_md = rep_dir / "merge_report.md"
    assert rep_md.exists()

def test_merge_missing_columns(tmp_path):
    pm25_path = tmp_path / "pm25.csv"
    weather_path = tmp_path / "weather.csv"

    pm25_df, weather_df = create_valid_dfs()
    weather_df = weather_df.drop(columns=["temperature_2m_c"])

    pm25_df.to_csv(pm25_path, index=False)
    weather_df.to_csv(weather_path, index=False)

    with pytest.raises(ValueError, match="missing required column: temperature_2m_c"):
        merge_pm25_weather(str(pm25_path), str(weather_path), str(tmp_path / "out.csv"), str(tmp_path / "report"))

def test_merge_duplicate_weather(tmp_path):
    pm25_path = tmp_path / "pm25.csv"
    weather_path = tmp_path / "weather.csv"

    pm25_df, weather_df = create_valid_dfs()
    # Add duplicate timestamp
    weather_df = pd.concat([weather_df, weather_df.iloc[[0]]], ignore_index=True)

    pm25_df.to_csv(pm25_path, index=False)
    weather_df.to_csv(weather_path, index=False)

    with pytest.raises(ValueError, match="Duplicate timestamps found"):
        merge_pm25_weather(str(pm25_path), str(weather_path), str(tmp_path / "out.csv"), str(tmp_path / "report"))
