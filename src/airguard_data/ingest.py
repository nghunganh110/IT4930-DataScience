"""Historical, month-chunked OpenAQ hourly PM2.5 ingestion."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from calendar import monthrange
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import pandas as pd

from .config import OpenAQIngestionConfig
from .http import HttpClient, RedactedException
from .normalize import apply_canonical_rules, create_empty_canonical_df
from .profile import profile_series
from .sources.openaq import BASE_URL


def _months(start: date, end: date) -> Iterable[Tuple[date, date]]:
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        chunk_start = start if (year, month) == (start.year, start.month) else date(year, month, 1)
        chunk_end = end if (year, month) == (end.year, end.month) else date(year, month, monthrange(year, month)[1])
        yield chunk_start, chunk_end
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
        handle.write(text)
        temp_name = handle.name
    os.replace(temp_name, path)


def _write_raw(raw_root: Path, month: str, page: int, response: Any, source_url: str) -> Dict[str, Any]:
    """Persist one real response with sidecar provenance; fixtures remain ephemeral."""
    content = getattr(response, "content", None)
    if not isinstance(content, bytes):
        return {"page": page, "persisted": False}
    directory = raw_root / month
    directory.mkdir(parents=True, exist_ok=True)
    raw_path = directory / f"hours_page_{page}.json"
    meta_path = directory / f"hours_page_{page}.json.meta.json"
    headers = getattr(response, "headers", {}) or {}
    metadata = {
        "source_url": source_url.split("?", 1)[0],
        "retrieval_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "etag": headers.get("ETag") if hasattr(headers, "get") else None,
        "last_modified": headers.get("Last-Modified") if hasattr(headers, "get") else None,
    }
    _atomic_bytes(raw_path, content)
    _atomic_text(meta_path, json.dumps(metadata, indent=2))
    return {"page": page, "raw_path": str(raw_path), "sha256": metadata["sha256"], "persisted": True}


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix=".tmp") as handle:
        handle.write(content)
        temp_name = handle.name
    os.replace(temp_name, path)


def _to_rows(results: List[Dict[str, Any]], config: OpenAQIngestionConfig) -> List[Dict[str, Any]]:
    rows = []
    for item in results:
        period = item.get("period") or {}
        dt = period.get("datetimeFrom") or {}
        timestamp_utc = dt.get("utc") if isinstance(dt, dict) else None
        if not timestamp_utc:
            continue
        parameter = item.get("parameter") or {}
        rows.append({
            "source": "openaq",
            "provider": "openaq",
            "location_id": config.location_id,
            "location_name": config.location_name,
            "latitude": config.latitude,
            "longitude": config.longitude,
            "timestamp_utc": timestamp_utc,
            "timestamp_local": dt.get("local") or timestamp_utc,
            "pm2_5_ug_m3": item.get("value"),
            "unit_original": parameter.get("units", "ug/m3"),
        })
    return rows


def ingest_openaq(config: OpenAQIngestionConfig) -> Tuple[int, Dict[str, Any]]:
    """Run all requested chunks and write canonical output/quality artifacts."""
    config.validate()
    if not config.openaq_api_key and not config.offline_fixtures:
        return 1, {"status": "blocked_auth", "message": "OPENAQ_API_KEY is not set."}

    client = HttpClient(config.timeout_seconds, config.offline_fixtures)
    if config.openaq_api_key:
        client.session.headers["X-API-Key"] = config.openaq_api_key
    url = f"{BASE_URL}/sensors/{config.sensor_id}/hours"
    raw_root = Path(config.raw_dir) / f"sensor_{config.sensor_id}"
    manifests: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []

    try:
        for chunk_start, chunk_end in _months(config.start_date, config.end_date):
            month_entry: Dict[str, Any] = {"month": chunk_start.strftime("%Y-%m"), "start": str(chunk_start), "end": str(chunk_end), "pages": []}
            for page in range(1, config.max_pages_per_month + 1):
                dt_from = f"{chunk_start}T00:00:00Z"
                dt_to = f"{(chunk_end + timedelta(days=1))}T00:00:00Z"
                response = client.get(url, params={"datetime_from": dt_from, "datetime_to": dt_to, "limit": 1000, "page": page})
                if response.status_code in (401, 403):
                    return 1, {"status": "blocked_auth", "message": "OpenAQ rejected the API key."}
                if response.status_code >= 400:
                    diag = ""
                    try:
                        body = response.json()
                        diag = body.get("message", "") if isinstance(body, dict) else str(body)
                    except Exception:
                        pass
                    msg = f"OpenAQ request failed (HTTP {response.status_code})"
                    if diag:
                        msg += f": {diag}"
                    return 1, {"status": "error", "message": msg}
                payload = response.json()
                results = payload.get("results")
                if not isinstance(results, list):
                    return 1, {"status": "error", "message": "OpenAQ hours response lacks a results list."}
                month_entry["pages"].append(_write_raw(raw_root, month_entry["month"], page, response, url))
                rows.extend(_to_rows(results, config))
                if len(results) < 1000:
                    break
            else:
                return 1, {"status": "error", "message": f"Pagination cap reached for {month_entry['month']}."}
            manifests.append(month_entry)
    except RedactedException as exc:
        return 1, {"status": "blocked_network", "message": str(exc)}
    except Exception:
        return 1, {"status": "error", "message": "OpenAQ ingestion failed (details omitted)."}

    raw_count = len(rows)
    canonical, rejected_units = apply_canonical_rules(pd.DataFrame(rows)) if rows else (create_empty_canonical_df(), 0)
    before_dedup = len(canonical)
    canonical = canonical.drop_duplicates(["source", "location_id", "timestamp_utc"], keep="first").sort_values(["source", "location_id", "timestamp_utc"])
    duplicate_count = before_dedup - len(canonical)
    valid_count = int(canonical["is_valid"].sum()) if not canonical.empty else 0
    profile, _ = profile_series(canonical, {"is_modeled": False, "verified_history_days": 0.0}) if not canonical.empty else ({"eligible": False, "reason": "empty"}, canonical)

    output = Path(config.output_dir)
    csv_path = output / "hourly_pm25.csv"
    _atomic_text(csv_path, canonical.to_csv(index=False))
    quality = {"requested_range": {"start": str(config.start_date), "end": str(config.end_date)}, "raw_row_count": raw_count, "normalized_row_count": len(canonical), "valid_row_count": valid_count, "rejected_unit_count": rejected_units, "duplicate_count": duplicate_count, "profile": profile}
    _atomic_text(output / "quality_report.json", json.dumps(quality, indent=2, default=str))
    _atomic_text(output / "quality_report.md", "# OpenAQ ingestion quality report\n\n" + f"- Requested range: {config.start_date} to {config.end_date}\n- Valid rows: {valid_count}\n- Coverage: {profile.get('coverage_ratio', 0):.2%}\n- Missing hours: {profile.get('missing_hour_count', 0)}\n- Longest gap: {profile.get('longest_consecutive_missing_hours', 0)} hours\n")
    manifest = {"schema_version": 1, "source": "openaq", "sensor_id": config.sensor_id, "location_id": config.location_id, "requested_range": quality["requested_range"], "retrieval_timestamp_utc": datetime.now(timezone.utc).isoformat(), "months": manifests, "counts": {k: quality[k] for k in ("raw_row_count", "normalized_row_count", "valid_row_count", "rejected_unit_count", "duplicate_count")}, "outputs": {"hourly_pm25.csv": _sha256_file(csv_path), "quality_report.json": _sha256_file(output / "quality_report.json")}}
    _atomic_text(output / "manifest.json", json.dumps(manifest, indent=2, default=str))
    return (0 if valid_count else 2), {"status": "completed" if valid_count else "no_data", "manifest": manifest}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
