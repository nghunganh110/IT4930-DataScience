import json
from datetime import date
from pathlib import Path
import tempfile
import pytest
from airguard_data.config import WeatherIngestionConfig
from airguard_data.weather import ingest_weather

def create_mock_open_meteo_response(path: Path, times=None, temp_len=None):
    path.mkdir(parents=True, exist_ok=True)

    if times is None:
        times = [f"2024-01-01T{i:02d}:00" for i in range(24)]

    n = len(times)

    if temp_len is None:
        temps = [20.0 + i for i in range(n)]
    else:
        temps = [20.0] * temp_len

    payload = {
        "latitude": 21.0,
        "longitude": 105.8,
        "elevation": 10.0,
        "hourly_units": {
            "time": "iso8601",
            "temperature_2m": "°C"
        },
        "hourly": {
            "time": times,
            "temperature_2m": temps,
            "relative_humidity_2m": [80] * n,
            "dew_point_2m": [15.0] * n,
            "precipitation": [0.0] * n,
            "surface_pressure": [1010.0] * n,
            "cloud_cover": [50] * n,
            "wind_speed_10m": [2.0] * n,
            "wind_direction_10m": [90] * n
        }
    }

    with open(path / "archive.json", "w") as f:
        json.dump(payload, f)
    with open(path / "v1_archive.json", "w") as f:
        json.dump(payload, f)

def test_ingest_weather_offline(tmp_path):
    fixtures_dir = Path(__file__).parent / "fixtures" / "weather"

    out_dir = tmp_path / "output"
    raw_dir = tmp_path / "raw"

    config = WeatherIngestionConfig(
        latitude=21.0,
        longitude=105.8,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 1),
        output_dir=str(out_dir),
        raw_dir=str(raw_dir),
        offline_fixtures=str(fixtures_dir)
    )

    code, result = ingest_weather(config)

    assert code == 0, f"Expected success, got: {result}"
    assert result["status"] == "completed"

    csv_path = out_dir / "hourly_weather.csv"
    assert csv_path.exists()

    content = csv_path.read_text()
    assert "timestamp_utc,temperature_2m_c" in content
    assert "2024-01-01T00:00:00Z,20.0" in content

    manifest_path = out_dir / "weather_manifest.json"
    assert manifest_path.exists()
    manifest_data = json.loads(manifest_path.read_text())
    # Verify manifest provenance fields
    assert manifest_data["source_endpoint"] == "https://archive-api.open-meteo.com/v1/archive"
    assert manifest_data["requested_coordinates"] == {"latitude": 21.0, "longitude": 105.8}
    assert "temperature_2m" in manifest_data["requested_variables"]
    assert manifest_data["unit_options"] == {"wind_speed_unit": "ms", "temperature_unit": "celsius", "precipitation_unit": "mm"}
    assert manifest_data["utc_timezone"] == "GMT"
    assert manifest_data["requested_date_range"] == {"start": "2024-01-01", "end": "2024-01-01"}

    # Verify raw provenance fields are complete
    year_entry = manifest_data["years"][0]
    meta_file = Path(year_entry["raw_path"]).parent / "response.json.meta.json"
    assert meta_file.exists()
    meta = json.loads(meta_file.read_text())
    assert "requested_dates" in meta
    assert "requested_variables" in meta
    assert "temperature_2m" in meta["requested_variables"]

    report_path = out_dir / "weather_quality_report.json"
    assert report_path.exists()

def test_gap_non_hourly_timestamp(tmp_path):
    fixtures_dir = tmp_path / "fixtures"
    # Create 24 items but one is wrong time
    times = [f"2024-01-01T{i:02d}:00" for i in range(24)]
    times[5] = "2024-01-01T05:30"  # Gap/non-hourly
    create_mock_open_meteo_response(fixtures_dir, times=times)

    config = WeatherIngestionConfig(
        latitude=21.0, longitude=105.8,
        start_date=date(2024, 1, 1), end_date=date(2024, 1, 1),
        output_dir=str(tmp_path / "out"), offline_fixtures=str(fixtures_dir)
    )
    code, res = ingest_weather(config)
    assert code == 1
    assert "exact unique hourly sequence" in res["message"]

def test_duplicate_timestamp(tmp_path):
    fixtures_dir = tmp_path / "fixtures"
    times = [f"2024-01-01T{i:02d}:00" for i in range(24)]
    times[5] = "2024-01-01T04:00"  # Duplicate 04:00
    create_mock_open_meteo_response(fixtures_dir, times=times)

    config = WeatherIngestionConfig(
        latitude=21.0, longitude=105.8,
        start_date=date(2024, 1, 1), end_date=date(2024, 1, 1),
        output_dir=str(tmp_path / "out"), offline_fixtures=str(fixtures_dir)
    )
    code, res = ingest_weather(config)
    assert code == 1
    assert "Duplicate timestamps" in res["message"]

def test_malformed_timestamp(tmp_path):
    fixtures_dir = tmp_path / "fixtures"
    times = [f"2024-01-01T{i:02d}:00" for i in range(24)]
    times[5] = "invalid-time"
    create_mock_open_meteo_response(fixtures_dir, times=times)

    config = WeatherIngestionConfig(
        latitude=21.0, longitude=105.8,
        start_date=date(2024, 1, 1), end_date=date(2024, 1, 1),
        output_dir=str(tmp_path / "out"), offline_fixtures=str(fixtures_dir)
    )
    code, res = ingest_weather(config)
    assert code == 1
    assert "Invalid/unparseable timestamps" in res["message"]

def test_wrong_length_variable(tmp_path):
    fixtures_dir = tmp_path / "fixtures"
    # Provide 24 times but 23 temps
    create_mock_open_meteo_response(fixtures_dir, temp_len=23)

    config = WeatherIngestionConfig(
        latitude=21.0, longitude=105.8,
        start_date=date(2024, 1, 1), end_date=date(2024, 1, 1),
        output_dir=str(tmp_path / "out"), offline_fixtures=str(fixtures_dir)
    )
    code, res = ingest_weather(config)
    assert code == 1
    assert "Malformed array for temperature_2m" in res["message"]

def test_partial_yearly_request(tmp_path):
    fixtures_dir = tmp_path / "fixtures"
    # Create request for 2 days (Jan 1-Jan 2)
    times = [f"2024-01-01T{i:02d}:00" for i in range(24)] + [f"2024-01-02T{i:02d}:00" for i in range(24)]
    create_mock_open_meteo_response(fixtures_dir, times=times)

    config = WeatherIngestionConfig(
        latitude=21.0, longitude=105.8,
        start_date=date(2024, 1, 1), end_date=date(2024, 1, 2),
        output_dir=str(tmp_path / "out"), offline_fixtures=str(fixtures_dir)
    )
    code, res = ingest_weather(config)
    assert code == 0
