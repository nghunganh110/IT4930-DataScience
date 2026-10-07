import json
import hashlib
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

from .config import WeatherIngestionConfig
from .http import HttpClient, RedactedException

OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"

def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
        handle.write(text)
        temp_name = handle.name
    os.replace(temp_name, path)

def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix=".tmp") as handle:
        handle.write(content)
        temp_name = handle.name
    os.replace(temp_name, path)

def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def _years(start: date, end: date) -> List[Tuple[date, date]]:
    chunks = []
    current_year = start.year
    while current_year <= end.year:
        chunk_start = start if current_year == start.year else date(current_year, 1, 1)
        chunk_end = end if current_year == end.year else date(current_year, 12, 31)
        chunks.append((chunk_start, chunk_end))
        current_year += 1
    return chunks

def _write_raw(
    raw_root: Path,
    year: int,
    response: Any,
    source_url: str,
    req_params: Dict[str, Any]
) -> Dict[str, Any]:
    content = getattr(response, "content", None)
    if not isinstance(content, bytes):
        return {"year": year, "persisted": False}

    directory = raw_root / str(year)
    directory.mkdir(parents=True, exist_ok=True)
    raw_path = directory / "response.json"
    meta_path = directory / "response.json.meta.json"

    headers = getattr(response, "headers", {}) or {}
    metadata = {
        "source_url": source_url,
        "requested_dates": {
            "start": req_params.get("start_date"),
            "end": req_params.get("end_date")
        },
        "requested_coordinates": {
            "latitude": req_params.get("latitude"),
            "longitude": req_params.get("longitude")
        },
        "requested_variables": req_params.get("hourly", "").split(","),
        "requested_units": {
            "wind_speed_unit": req_params.get("wind_speed_unit"),
            "temperature_unit": req_params.get("temperature_unit"),
            "precipitation_unit": req_params.get("precipitation_unit")
        },
        "retrieval_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "etag": headers.get("ETag") if hasattr(headers, "get") else None,
        "last_modified": headers.get("Last-Modified") if hasattr(headers, "get") else None,
    }

    _atomic_bytes(raw_path, content)
    _atomic_text(meta_path, json.dumps(metadata, indent=2))

    return {
        "year": year,
        "raw_path": str(raw_path),
        "sha256": metadata["sha256"],
        "persisted": True
    }

def ingest_weather(config: WeatherIngestionConfig) -> Tuple[int, Dict[str, Any]]:
    config.validate()

    client = HttpClient(config.timeout_seconds, config.offline_fixtures)

    location_slug = f"lat_{config.latitude}_lon_{config.longitude}".replace(".", "_")
    raw_root = Path(config.raw_dir) / location_slug

    manifests: List[Dict[str, Any]] = []
    all_data = []

    returned_lat = None
    returned_lon = None
    returned_elev = None
    returned_units = None

    hourly_vars = [
        "temperature_2m",
        "relative_humidity_2m",
        "dew_point_2m",
        "precipitation",
        "surface_pressure",
        "cloud_cover",
        "wind_speed_10m",
        "wind_direction_10m"
    ]

    try:
        for chunk_start, chunk_end in _years(config.start_date, config.end_date):
            params = {
                "latitude": config.latitude,
                "longitude": config.longitude,
                "start_date": str(chunk_start),
                "end_date": str(chunk_end),
                "hourly": ",".join(hourly_vars),
                "timezone": "GMT",
                "wind_speed_unit": "ms",
                "temperature_unit": "celsius",
                "precipitation_unit": "mm"
            }

            response = client.get(OPEN_METEO_URL, params=params)
            if response.status_code >= 400:
                diag = ""
                try:
                    body = response.json()
                    diag = body.get("reason", "") if isinstance(body, dict) else str(body)
                except Exception:
                    pass
                msg = f"Open-Meteo request failed (HTTP {response.status_code})"
                if diag:
                    msg += f": {diag}"
                return 1, {"status": "error", "message": msg}

            payload = response.json()
            if "hourly" not in payload or "time" not in payload["hourly"]:
                return 1, {"status": "error", "message": "Missing hourly.time in response"}

            hourly = payload["hourly"]
            times = hourly["time"]
            n = len(times)

            # Check length of all vars
            for var in hourly_vars:
                if var not in hourly:
                    return 1, {"status": "error", "message": f"Missing variable {var} in response"}
                if len(hourly[var]) != n:
                    return 1, {"status": "error", "message": f"Malformed array for {var}, expected {n} elements"}

            df = pd.DataFrame(hourly)
            all_data.append(df)

            if returned_lat is None:
                returned_lat = payload.get("latitude")
                returned_lon = payload.get("longitude")
                returned_elev = payload.get("elevation")
                returned_units = payload.get("hourly_units")

            manifest_entry = _write_raw(raw_root, chunk_start.year, response, OPEN_METEO_URL, params)
            manifest_entry["start_date"] = str(chunk_start)
            manifest_entry["end_date"] = str(chunk_end)
            manifests.append(manifest_entry)

    except RedactedException as exc:
        return 1, {"status": "blocked_network", "message": str(exc)}
    except Exception as exc:
        return 1, {"status": "error", "message": f"Open-Meteo ingestion failed: {exc}"}

    if not all_data:
        return 1, {"status": "error", "message": "No rows returned"}

    full_df = pd.concat(all_data, ignore_index=True)
    full_df.rename(columns={
        "time": "timestamp_utc",
        "temperature_2m": "temperature_2m_c",
        "relative_humidity_2m": "relative_humidity_2m_pct",
        "dew_point_2m": "dew_point_2m_c",
        "precipitation": "precipitation_mm",
        "surface_pressure": "surface_pressure_hpa",
        "cloud_cover": "cloud_cover_pct",
        "wind_speed_10m": "wind_speed_10m_ms",
        "wind_direction_10m": "wind_direction_10m_deg"
    }, inplace=True)

    # Convert timestamps explicitly to UTC and reject invalid ones
    try:
        full_df["timestamp_utc"] = pd.to_datetime(full_df["timestamp_utc"], utc=True, format="ISO8601")
    except Exception:
        # Fallback to mixed parsing if format strictly fails, but validate non-null
        full_df["timestamp_utc"] = pd.to_datetime(full_df["timestamp_utc"], utc=True, errors="coerce")

    if full_df["timestamp_utc"].isnull().any():
        return 1, {"status": "error", "message": "Invalid/unparseable timestamps found"}

    # Sort just in case
    full_df.sort_values("timestamp_utc", inplace=True)

    # Check duplicates
    if full_df["timestamp_utc"].duplicated().any():
        return 1, {"status": "error", "message": "Duplicate timestamps found"}

    # Compute expected coverage from requested dates
    expected_start = pd.to_datetime(config.start_date).tz_localize('UTC')
    expected_end = pd.to_datetime(config.end_date).tz_localize('UTC') + pd.Timedelta(hours=23)
    expected_hours = int((expected_end - expected_start).total_seconds() / 3600) + 1

    expected_range = pd.date_range(start=expected_start, end=expected_end, freq="h")

    if len(full_df) != len(expected_range):
        return 1, {"status": "error", "message": f"Does not cover exact requested range. Expected {len(expected_range)} hours, got {len(full_df)}"}

    # Verify exact unique hourly sequence
    if not (full_df["timestamp_utc"].values == expected_range.values).all():
        return 1, {"status": "error", "message": "Timestamps do not form an exact unique hourly sequence covering the requested range"}

    # Format timestamp explicitly to UTC iso format
    full_df["timestamp_utc"] = full_df["timestamp_utc"].dt.strftime('%Y-%m-%dT%H:%M:%SZ')

    # Reorder columns
    col_order = [
        "timestamp_utc",
        "temperature_2m_c",
        "relative_humidity_2m_pct",
        "dew_point_2m_c",
        "precipitation_mm",
        "surface_pressure_hpa",
        "cloud_cover_pct",
        "wind_speed_10m_ms",
        "wind_direction_10m_deg"
    ]
    full_df = full_df[col_order]

    output = Path(config.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    csv_path = output / "hourly_weather.csv"
    _atomic_text(csv_path, full_df.to_csv(index=False))

    total_rows = len(full_df)
    null_counts = full_df.isnull().sum().to_dict()
    mins = full_df.drop(columns=["timestamp_utc"]).min(numeric_only=True).to_dict()
    maxs = full_df.drop(columns=["timestamp_utc"]).max(numeric_only=True).to_dict()

    quality = {
        "expected_coverage": expected_hours,
        "observed_coverage": total_rows,
        "duplicate_count": 0,
        "null_counts": null_counts,
        "ranges": {k: {"min": mins.get(k), "max": maxs.get(k)} for k in mins},
        "source_coordinates": {
            "latitude": returned_lat,
            "longitude": returned_lon,
            "elevation": returned_elev
        }
    }

    _atomic_text(output / "weather_quality_report.json", json.dumps(quality, indent=2, default=str))

    md_report = "# Weather Ingestion Quality Report\n\n"
    md_report += f"- **Expected Hours**: {expected_hours}\n"
    md_report += f"- **Observed Hours**: {total_rows}\n"
    md_report += f"- **Duplicate Timestamps**: 0\n"
    md_report += f"- **Source Coordinates**: Lat {returned_lat}, Lon {returned_lon}, Elev {returned_elev}\n\n"
    md_report += "### Null Counts\n"
    for col, count in null_counts.items():
        md_report += f"- {col}: {count}\n"
    _atomic_text(output / "weather_quality_report.md", md_report)

    manifest = {
        "schema_version": 1,
        "source": "open_meteo",
        "source_endpoint": OPEN_METEO_URL,
        "requested_coordinates": {
            "latitude": config.latitude,
            "longitude": config.longitude
        },
        "requested_variables": hourly_vars,
        "unit_options": {
            "wind_speed_unit": "ms",
            "temperature_unit": "celsius",
            "precipitation_unit": "mm"
        },
        "utc_timezone": "GMT",
        "requested_date_range": {"start": str(config.start_date), "end": str(config.end_date)},
        "retrieval_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "returned_coordinates": {
            "latitude": returned_lat,
            "longitude": returned_lon,
            "elevation": returned_elev
        },
        "returned_units": returned_units,
        "years": manifests,
        "counts": {
            "total_rows": total_rows
        },
        "outputs": {
            "hourly_weather.csv": _sha256_file(csv_path),
            "weather_quality_report.json": _sha256_file(output / "weather_quality_report.json")
        }
    }

    _atomic_text(output / "weather_manifest.json", json.dumps(manifest, indent=2, default=str))

    return 0, {"status": "completed", "manifest": manifest}
