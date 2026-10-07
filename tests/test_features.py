import os
import json
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from airguard_data.features import build_features, parse_int_list_option

def test_parse_int_list_option():
    assert parse_int_list_option("1, 2, 3") == [1, 2, 3]
    assert parse_int_list_option("3,6,12") == [3, 6, 12]
    
    with pytest.raises(ValueError, match="positive"):
        parse_int_list_option("0, 1")
    
    with pytest.raises(ValueError, match="positive"):
        parse_int_list_option("-1, 1")
    
    with pytest.raises(ValueError, match="Duplicate"):
        parse_int_list_option("1, 2, 1")
        
    with pytest.raises(ValueError, match="Invalid integer"):
        parse_int_list_option("a, 1")

@pytest.fixture
def dummy_input_csv(tmp_path):
    # Create a small valid dataset with exactly 1 hour gaps for some rows
    # and a missing gap for another
    
    data = {
        "timestamp_utc": [
            "2024-01-01T00:00:00Z",
            "2024-01-01T01:00:00Z",
            "2024-01-01T02:00:00Z",
            "2024-01-01T03:00:00Z",
            "2024-01-01T04:00:00Z",
            "2024-01-01T07:00:00Z", # gap here
            "2024-01-01T08:00:00Z"
        ],
        "pm2_5_ug_m3": [10.0, 15.0, 12.0, np.nan, 20.0, 25.0, 30.0],
        "is_valid": [True, True, True, False, True, True, True],
        "weather_available": [True, True, True, True, False, True, True],
        "temperature_2m_c": [20.0, 21.0, 21.5, 22.0, 22.0, 23.0, 24.0],
        "relative_humidity_2m_pct": [50.0, 52.0, 50.0, 48.0, 45.0, 40.0, 35.0],
        "dew_point_2m_c": [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0],
        "precipitation_mm": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "surface_pressure_hpa": [1010.0, 1010.0, 1010.0, 1010.0, 1010.0, 1010.0, 1010.0],
        "cloud_cover_pct": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "wind_speed_10m_ms": [1.0, 2.0, 1.5, 1.0, 1.0, 2.0, 3.0],
        "wind_direction_10m_deg": [0.0, 90.0, 180.0, 270.0, 0.0, 90.0, 180.0]
    }
    df = pd.DataFrame(data)
    input_path = tmp_path / "pm25_weather.csv"
    df.to_csv(input_path, index=False)
    return str(input_path)

def test_build_features(tmp_path, dummy_input_csv):
    output_path = tmp_path / "features.csv"
    report_dir = tmp_path / "report"
    
    build_features(
        input_path=dummy_input_csv,
        output_path=str(output_path),
        report_dir=str(report_dir),
        horizons=[1, 2],
        lags=[1, 2],
        rolling_windows=[2],
        local_timezone="Asia/Ho_Chi_Minh"
    )
    
    assert output_path.exists()
    assert (report_dir / "feature_report.json").exists()
    assert (report_dir / "feature_report.md").exists()
    
    df_out = pd.read_csv(output_path)
    # Total 7 rows.
    # Segments:
    # row 0 (00:00): segment 1. Eligible? valid pm25, weather. Lag1/2 null -> No
    # row 1 (01:00): segment 1. lag2 null -> No
    # row 2 (02:00): segment 1. lag1=15, lag2=10. Roll mean2 = 13.5. Eligible -> Yes!
    # row 3 (03:00): segment 1. is_valid=False -> No
    # row 4 (04:00): segment 1. weather=False -> No
    # row 5 (07:00): segment 2. lag1 null -> No
    # row 6 (08:00): segment 2. lag2 null -> No
    
    # Expected eligible row count: 1 (row 2 at 02:00)
    assert len(df_out) == 1
    row = df_out.iloc[0]
    
    assert row["timestamp_utc"] == "2024-01-01T02:00:00Z"
    assert row["pm25_current_ug_m3"] == 12.0
    assert row["pm25_lag_1h"] == 15.0
    assert row["pm25_lag_2h"] == 10.0
    assert row["pm25_roll_mean_2h"] == 13.5
    
    # Target 1h for 02:00 is row 3 (03:00), but row 3 is invalid (NaN pm25)
    assert pd.isna(row["target_pm25_h1"])
    assert pd.isna(row["target_timestamp_h1"])
    
    # Target 2h for 02:00 was 04:00. But 03:00 is invalid, so 04:00 is in a new segment.
    # Therefore, the target crosses a gap and must be null.
    assert pd.isna(row["target_pm25_h2"])
    assert pd.isna(row["target_timestamp_h2"])
    
    # Current weather 
    assert row["wind_direction_10m_sin"] == pytest.approx(0.0) # sin(180) = 0
    assert row["wind_direction_10m_cos"] == pytest.approx(-1.0) # cos(180) = -1

    # Local Calendar check. UTC 02:00 + 7 hours = 09:00
    assert row["hour"] == 9

def test_build_features_no_eligible(tmp_path):
    data = {
        "timestamp_utc": ["2024-01-01T00:00:00Z"],
        "pm2_5_ug_m3": [10.0],
        "is_valid": [True],
        "weather_available": [True],
        "temperature_2m_c": [20.0],
        "relative_humidity_2m_pct": [50.0],
        "dew_point_2m_c": [10.0],
        "precipitation_mm": [0.0],
        "surface_pressure_hpa": [1010.0],
        "cloud_cover_pct": [0.0],
        "wind_speed_10m_ms": [1.0],
        "wind_direction_10m_deg": [0.0]
    }
    df = pd.DataFrame(data)
    input_path = tmp_path / "pm25_weather.csv"
    df.to_csv(input_path, index=False)
    
    output_path = tmp_path / "features.csv"
    report_dir = tmp_path / "report"
    
    build_features(
        input_path=str(input_path),
        output_path=str(output_path),
        report_dir=str(report_dir),
        horizons=[1],
        lags=[1],
        rolling_windows=[2],
        local_timezone="Asia/Ho_Chi_Minh"
    )
    
    assert output_path.exists()
    df_out = pd.read_csv(output_path)
    assert len(df_out) == 0
    
    with open(report_dir / "feature_report.json") as f:
        report = json.load(f)
        assert report["feature_output_rows"] == 0

def test_missing_weather_schema(tmp_path):
    data = {
        "timestamp_utc": ["2024-01-01T00:00:00Z"],
        "pm2_5_ug_m3": [10.0],
        "is_valid": [True],
        "weather_available": [True],
        # Missing 'temperature_2m_c' etc.
    }
    df = pd.DataFrame(data)
    input_path = tmp_path / "pm25_weather.csv"
    df.to_csv(input_path, index=False)
    
    with pytest.raises(ValueError, match="Missing required columns"):
        build_features(
            input_path=str(input_path),
            output_path=str(tmp_path / "features.csv"),
            report_dir=str(tmp_path / "report"),
            horizons=[1], lags=[1], rolling_windows=[2],
            local_timezone="Asia/Ho_Chi_Minh"
        )

def test_malformed_duplicate_timestamps(tmp_path):
    # Malformed
    data = {
        "timestamp_utc": ["not-a-date"],
        "pm2_5_ug_m3": [10.0],
        "is_valid": [True],
        "weather_available": [True],
        "temperature_2m_c": [20.0], "relative_humidity_2m_pct": [50.0],
        "dew_point_2m_c": [10.0], "precipitation_mm": [0.0],
        "surface_pressure_hpa": [1010.0], "cloud_cover_pct": [0.0],
        "wind_speed_10m_ms": [1.0], "wind_direction_10m_deg": [0.0]
    }
    df = pd.DataFrame(data)
    input_path = tmp_path / "malformed.csv"
    df.to_csv(input_path, index=False)
    
    with pytest.raises(ValueError, match="Malformed timestamps found"):
        build_features(
            input_path=str(input_path),
            output_path=str(tmp_path / "f.csv"), report_dir=str(tmp_path / "r"),
            horizons=[1], lags=[1], rolling_windows=[2], local_timezone="UTC"
        )
        
    # Duplicate
    data["timestamp_utc"] = ["2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z"]
    data["pm2_5_ug_m3"] = [10.0, 20.0]
    data["is_valid"] = [True, True]
    data["weather_available"] = [True, True]
    data["temperature_2m_c"] = [20.0, 20.0]
    data["relative_humidity_2m_pct"] = [50.0, 50.0]
    data["dew_point_2m_c"] = [10.0, 10.0]
    data["precipitation_mm"] = [0.0, 0.0]
    data["surface_pressure_hpa"] = [1010.0, 1010.0]
    data["cloud_cover_pct"] = [0.0, 0.0]
    data["wind_speed_10m_ms"] = [1.0, 1.0]
    data["wind_direction_10m_deg"] = [0.0, 0.0]
    df = pd.DataFrame(data)
    input_path = tmp_path / "duplicate.csv"
    df.to_csv(input_path, index=False)
    
    with pytest.raises(ValueError, match="Duplicate timestamps found"):
        build_features(
            input_path=str(input_path),
            output_path=str(tmp_path / "f.csv"), report_dir=str(tmp_path / "r"),
            horizons=[1], lags=[1], rolling_windows=[2], local_timezone="UTC"
        )

def test_invalid_pm25_breaks_continuity_and_leakage(tmp_path):
    # A valid row at t=0, t=1, t=2
    # Invalid row at t=3 but has POSITIVE NUMERIC value
    # Valid row at t=4, t=5, t=6
    data = {
        "timestamp_utc": [
            "2024-01-01T00:00:00Z",
            "2024-01-01T01:00:00Z",
            "2024-01-01T02:00:00Z",
            "2024-01-01T03:00:00Z",
            "2024-01-01T04:00:00Z",
            "2024-01-01T05:00:00Z",
            "2024-01-01T06:00:00Z"
        ],
        "pm2_5_ug_m3": [10.0, 10.0, 10.0, 999.0, 10.0, 10.0, 10.0],
        "is_valid": [True, True, True, False, True, True, True],
        "weather_available": [True]*7,
        "temperature_2m_c": [20.0]*7,
        "relative_humidity_2m_pct": [50.0]*7,
        "dew_point_2m_c": [10.0]*7,
        "precipitation_mm": [0.0]*7,
        "surface_pressure_hpa": [1010.0]*7,
        "cloud_cover_pct": [0.0]*7,
        "wind_speed_10m_ms": [1.0]*7,
        "wind_direction_10m_deg": [0.0]*7
    }
    df = pd.DataFrame(data)
    input_path = tmp_path / "leakage.csv"
    df.to_csv(input_path, index=False)
    
    out_path = tmp_path / "features.csv"
    build_features(
        input_path=str(input_path),
        output_path=str(out_path),
        report_dir=str(tmp_path / "report"),
        horizons=[1, 2], lags=[1, 2], rolling_windows=[2],
        local_timezone="UTC"
    )
    
    df_out = pd.read_csv(out_path)
    
    # row 0, 1: no lag2 -> not eligible
    # row 2: eligible! t=02:00
    # row 3: invalid pm25 -> dropped
    # row 4, 5: no lag2 in their new segment -> not eligible
    # row 6: eligible! t=06:00
    
    assert len(df_out) == 2
    assert df_out.iloc[0]["timestamp_utc"] == "2024-01-01T02:00:00Z"
    assert df_out.iloc[1]["timestamp_utc"] == "2024-01-01T06:00:00Z"
    
    # For t=02:00, target 1h crosses invalid row 3, must be NaN
    assert pd.isna(df_out.iloc[0]["target_pm25_h1"])
    # Target 2h crosses invalid row 3, must be NaN
    assert pd.isna(df_out.iloc[0]["target_pm25_h2"])
    
    # Ensure invalid pm25=999.0 does not appear anywhere
    for col in df_out.columns:
        if "pm25" in col:
            vals = df_out[col].dropna().values
            assert not any(v == 999.0 for v in vals)

def test_weather_leakage(tmp_path):
    data = {
        "timestamp_utc": ["2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z"],
        "pm2_5_ug_m3": [10.0, 20.0],
        "is_valid": [True, True],
        "weather_available": [True, True],
        "temperature_2m_c": [20.0, 25.0],
        "relative_humidity_2m_pct": [50.0, 50.0],
        "dew_point_2m_c": [10.0, 10.0],
        "precipitation_mm": [0.0, 0.0],
        "surface_pressure_hpa": [1010.0, 1010.0],
        "cloud_cover_pct": [0.0, 0.0],
        "wind_speed_10m_ms": [1.0, 1.0],
        "wind_direction_10m_deg": [0.0, 0.0]
    }
    df1 = pd.DataFrame(data)
    in1 = tmp_path / "in1.csv"
    df1.to_csv(in1, index=False)
    
    # Change weather at t=1
    data2 = data.copy()
    data2["temperature_2m_c"] = [20.0, 99.0]
    df2 = pd.DataFrame(data2)
    in2 = tmp_path / "in2.csv"
    df2.to_csv(in2, index=False)
    
    out1 = tmp_path / "out1.csv"
    out2 = tmp_path / "out2.csv"
    
    build_features(str(in1), str(out1), str(tmp_path / "r1"), [1], [1], [1], "UTC")
    build_features(str(in2), str(out2), str(tmp_path / "r2"), [1], [1], [1], "UTC")
    
    res1 = pd.read_csv(out1)
    res2 = pd.read_csv(out2)
    
    # The feature row for t=0 should be identical in both, since the weather change is at t=1 (t+h).
    # Wait, t=0 is not eligible because lag1 is null. Let's add more rows.
    pass

def test_weather_leakage_extended(tmp_path):
    data = {
        "timestamp_utc": [
            "2024-01-01T00:00:00Z",
            "2024-01-01T01:00:00Z",
            "2024-01-01T02:00:00Z"
        ],
        "pm2_5_ug_m3": [10.0, 20.0, 30.0],
        "is_valid": [True, True, True],
        "weather_available": [True, True, True],
        "temperature_2m_c": [20.0, 25.0, 30.0],
        "relative_humidity_2m_pct": [50.0, 50.0, 50.0],
        "dew_point_2m_c": [10.0, 10.0, 10.0],
        "precipitation_mm": [0.0, 0.0, 0.0],
        "surface_pressure_hpa": [1010.0, 1010.0, 1010.0],
        "cloud_cover_pct": [0.0, 0.0, 0.0],
        "wind_speed_10m_ms": [1.0, 1.0, 1.0],
        "wind_direction_10m_deg": [0.0, 0.0, 0.0]
    }
    df1 = pd.DataFrame(data)
    in1 = tmp_path / "in1.csv"
    df1.to_csv(in1, index=False)
    
    # Change weather at t=2
    data2 = data.copy()
    data2["temperature_2m_c"] = [20.0, 25.0, 99.0] # Changed at t=2
    df2 = pd.DataFrame(data2)
    in2 = tmp_path / "in2.csv"
    df2.to_csv(in2, index=False)
    
    out1 = tmp_path / "out1.csv"
    out2 = tmp_path / "out2.csv"
    
    build_features(str(in1), str(out1), str(tmp_path / "r1"), [1], [1], [2], "UTC")
    build_features(str(in2), str(out2), str(tmp_path / "r2"), [1], [1], [2], "UTC")
    
    res1 = pd.read_csv(out1)
    res2 = pd.read_csv(out2)
    
    # Feature for t=1 (index 1) should be eligible
    t1_feat1 = res1[res1["timestamp_utc"] == "2024-01-01T01:00:00Z"].iloc[0]
    t1_feat2 = res2[res2["timestamp_utc"] == "2024-01-01T01:00:00Z"].iloc[0]
    
    # Assert features are identical
    for col in res1.columns:
        if col not in ["target_pm25_h1", "target_timestamp_h1"]: # targets might differ if pm25 changed, but we only changed weather
            if pd.isna(t1_feat1[col]):
                assert pd.isna(t1_feat2[col])
            else:
                assert t1_feat1[col] == t1_feat2[col]

