"""
OpenAQ v3 adapter.

API reference: https://docs.openaq.org/reference/

Key endpoints used:
  GET /v3/locations?coordinates=lat,lon&radius=r&parameters_id=2  (PM2.5 = parameter id 2)
  GET /v3/sensors/{sensor_id}                                      (sensor detail / first-last)
  GET /v3/sensors/{sensor_id}/hours?datetime_from=...&datetime_to=...  (hourly aggregates)
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import pandas as pd

from ..contracts import SourceAdapter, SourceProbeResult, SourceStatus
from ..config import FeasibilityConfig
from ..http import HttpClient, RedactedException, _redact_url
from ..normalize import apply_canonical_rules

# OpenAQ PM2.5 parameter id
_PM25_PARAM_ID = 2
_PM25_PARAM_NAMES = {"pm25", "pm2.5"}

BASE_URL = "https://api.openaq.org/v3"

# Hard cap on pagination loops to prevent infinite loops
_MAX_PAGES = 50


class OpenAQAdapter(SourceAdapter):
    def probe(self, config: FeasibilityConfig) -> SourceProbeResult:
        # Require API key unless running in offline-fixture mode
        if not config.openaq_api_key and not config.offline_fixtures:
            return SourceProbeResult(
                source="openaq",
                status=SourceStatus.BLOCKED_AUTH,
                message="OPENAQ_API_KEY is not set. Provide the environment variable to enable probing.",
                observations=pd.DataFrame(),
                metadata={"is_modeled": False},
            )

        client = HttpClient(config.timeout_seconds, config.offline_fixtures)
        if config.openaq_api_key:
            client.session.headers["X-API-Key"] = config.openaq_api_key

        try:
            return self._probe_internal(config, client)
        except RedactedException as exc:
            return SourceProbeResult(
                source="openaq",
                status=SourceStatus.BLOCKED_NETWORK,
                message=f"Network error: {exc}",
                observations=pd.DataFrame(),
                metadata={"is_modeled": False},
            )
        except Exception:
            return SourceProbeResult(
                source="openaq",
                status=SourceStatus.ERROR,
                message="Internal adapter error (details omitted to prevent secret leakage)",
                observations=pd.DataFrame(),
                metadata={"is_modeled": False},
            )

    # ------------------------------------------------------------------
    # Internal probe
    # ------------------------------------------------------------------

    def _probe_internal(
        self,
        config: FeasibilityConfig,
        client: HttpClient,
        ranked_candidates: Optional[List[Dict[str, Any]]] = None,
        candidate_index: int = 0,
        candidate_attempts: Optional[List[Dict[str, Any]]] = None,
    ) -> SourceProbeResult:

        # ── Step 1: discover and rank locations with PM2.5 near Hanoi ────
        if ranked_candidates is None:
            loc_url = f"{BASE_URL}/locations"
            params: Dict[str, Any] = {
                "coordinates": f"{config.latitude},{config.longitude}",
                "radius": config.radius_m,
                "parameters_id": _PM25_PARAM_ID,
                "limit": 100,
            }

            resp = client.get(loc_url, params=params)
            status_code = getattr(resp, "status_code", 200)

            if status_code in (401, 403):
                return SourceProbeResult(
                    source="openaq",
                    status=SourceStatus.BLOCKED_AUTH,
                    message="OpenAQ API returned 401/403. Check OPENAQ_API_KEY.",
                    observations=pd.DataFrame(),
                    metadata={"is_modeled": False},
                )
            if status_code >= 400:
                return SourceProbeResult(
                    source="openaq",
                    status=SourceStatus.ERROR,
                    message=f"OpenAQ locations endpoint returned HTTP {status_code}",
                    observations=pd.DataFrame(),
                    metadata={"is_modeled": False},
                )

            try:
                loc_data = resp.json()
            except Exception:
                return SourceProbeResult(
                    source="openaq",
                    status=SourceStatus.ERROR,
                    message="Failed to parse locations response as JSON",
                    observations=pd.DataFrame(),
                    metadata={"is_modeled": False},
                )

            if not config.offline_fixtures:
                _persist_raw_response("locations", loc_url, resp)

            # Find all locations with a PM2.5 sensor and order them by useful
            # declared history. API order is not a suitability guarantee.
            candidates: List[Dict[str, Any]] = []
            for loc in loc_data.get("results", []):
                for sensor in loc.get("sensors", []):
                    param = sensor.get("parameter", {})
                    param_name = str(param.get("name", "")).lower()
                    param_id = param.get("id")
                    if param_name in _PM25_PARAM_NAMES or param_id == _PM25_PARAM_ID:
                        candidates.append({"loc": loc, "sensor": sensor})
            ranked_candidates = _rank_candidates(candidates, config)

        if not ranked_candidates:
            return SourceProbeResult(
                source="openaq",
                status=SourceStatus.INELIGIBLE,
                message=f"No PM2.5 locations found within {config.radius_m} m of Hanoi center",
                observations=pd.DataFrame(),
                metadata={"is_modeled": False},
            )

        candidate_attempts = candidate_attempts if candidate_attempts is not None else []
        target = ranked_candidates[candidate_index]
        loc = target["loc"]
        sensor = target["sensor"]

        loc_id = str(loc["id"])
        sensor_id = str(sensor["id"])
        loc_name = loc.get("name", loc_id)
        coords = loc.get("coordinates", {})
        lat = coords.get("latitude")
        lon = coords.get("longitude")

        # ── Step 2: fetch full sensor metadata for first/last timestamps ──
        verified_history_days: float = 0.0
        meta_first: Optional[str] = None
        meta_last: Optional[str] = None

        sensor_url = f"{BASE_URL}/sensors/{sensor_id}"
        try:
            sensor_resp = client.get(sensor_url)
            sensor_sc = getattr(sensor_resp, "status_code", 200)
            if sensor_sc == 200:
                sensor_data = sensor_resp.json()
                if not config.offline_fixtures:
                    _persist_raw_response(f"sensor_{sensor_id}", sensor_url, sensor_resp)
                # Official v3 sensor response shape:
                # { "results": [{ "datetimeFirst": {"utc": "..."}, "datetimeLast": {...} }] }
                results = sensor_data.get("results", [])
                if results:
                    r = results[0]
                    meta_first = (r.get("datetimeFirst") or {}).get("utc")
                    meta_last = (r.get("datetimeLast") or {}).get("utc")
                    if meta_first and meta_last:
                        try:
                            dt_from = datetime.fromisoformat(
                                meta_first.replace("Z", "+00:00")
                            )
                            dt_to = datetime.fromisoformat(
                                meta_last.replace("Z", "+00:00")
                            )
                            verified_history_days = (
                                dt_to - dt_from
                            ).total_seconds() / 86_400.0
                        except Exception:
                            verified_history_days = 0.0
        except Exception:
            # Non-fatal; proceed without verified history
            pass

        metadata: Dict[str, Any] = {
            "location_id": loc_id,
            "sensor_id": sensor_id,
            "location_name": loc_name,
            "is_modeled": False,
            "verified_history_days": verified_history_days,
            "metadata_first_utc": meta_first,
            "metadata_last_utc": meta_last,
            "all_candidates": len(ranked_candidates),
            "candidate_attempts": candidate_attempts,
        }

        # ── Step 3: fetch hourly measurements ────────────────────────────
        hours_url = f"{BASE_URL}/sensors/{sensor_id}/hours"
        # The precomputed `/hours` resource specifically names these filters
        # `datetime_from` and `datetime_to` (unlike some other aggregations).
        datetime_from = f"{config.start_date}T00:00:00Z"
        datetime_to = f"{(config.end_date + timedelta(days=1))}T00:00:00Z"

        page = 1
        limit = 1000
        all_rows: List[Dict[str, Any]] = []
        seen_page_checksums: set = set()

        while page <= _MAX_PAGES:
            m_params: Dict[str, Any] = {
                "datetime_from": datetime_from,
                "datetime_to": datetime_to,
                "limit": limit,
                "page": page,
            }
            try:
                m_resp = client.get(hours_url, params=m_params)
            except RedactedException as exc:
                metadata["pagination_error"] = str(exc)
                break

            m_sc = getattr(m_resp, "status_code", 200)
            if m_sc in (401, 403):
                return SourceProbeResult(
                    source="openaq",
                    status=SourceStatus.BLOCKED_AUTH,
                    message="OpenAQ API returned 401/403 on measurements endpoint.",
                    observations=pd.DataFrame(),
                    metadata=metadata,
                )
            if m_sc >= 400:
                diag = ""
                try:
                    diag = m_resp.json().get("message", "")
                except Exception:
                    pass
                msg = f"HTTP {m_sc} on page {page}"
                if diag:
                    msg += f": {diag}"
                metadata["hours_endpoint_error"] = msg
                break

            try:
                m_data = m_resp.json()
            except Exception:
                metadata["hours_parse_error"] = f"page {page}"
                break

            if not config.offline_fixtures:
                _persist_raw_response(
                    f"hours_{sensor_id}_page_{page}", hours_url, m_resp
                )

            results = m_data.get("results", [])
            if not results:
                break

            # Detect repeated pages (pagination loop guard)
            page_key = str(sorted(str(r) for r in results[:5]))
            if page_key in seen_page_checksums:
                metadata["pagination_repeated_page"] = page
                break
            seen_page_checksums.add(page_key)

            for row in results:
                # v3 /hours response:
                # { "period": { "datetimeFrom": {"utc": "..."}, "datetimeTo": {...} }, "value": 15.0 }
                period = row.get("period", {})
                dt_from_obj = period.get("datetimeFrom", {})
                ts_utc = (
                    dt_from_obj.get("utc")
                    if isinstance(dt_from_obj, dict)
                    else None
                )
                ts_local = (
                    dt_from_obj.get("local")
                    if isinstance(dt_from_obj, dict)
                    else None
                )
                if not ts_utc:
                    continue

                val = row.get("value")

                all_rows.append(
                    {
                        "source": "openaq",
                        "provider": "openaq",
                        "location_id": loc_id,
                        "location_name": loc_name,
                        "latitude": lat,
                        "longitude": lon,
                        "timestamp_utc": ts_utc,
                        "timestamp_local": ts_local or ts_utc,
                        "pm2_5_ug_m3": float(val) if val is not None else None,
                        "unit_original": (
                            sensor.get("parameter", {}).get("units", "ug/m3")
                        ),
                    }
                )

            # Check if there are more pages via meta
            total = m_data.get("meta", {}).get("found", None)
            try:
                total_count = int(total) if total is not None else None
            except (TypeError, ValueError):
                total_count = None
            if total_count is not None and len(all_rows) >= total_count:
                break
            if len(results) < limit:
                break
            page += 1

        if page > _MAX_PAGES:
            metadata["pagination_capped"] = True
            metadata["pagination_cap"] = _MAX_PAGES

        metadata["downloaded_row_count"] = len(all_rows)

        if not all_rows:
            candidate_attempts.append(
                _candidate_attempt(loc, sensor, "empty_window")
            )
            if candidate_index + 1 < len(ranked_candidates):
                return self._probe_internal(
                    config,
                    client,
                    ranked_candidates=ranked_candidates,
                    candidate_index=candidate_index + 1,
                    candidate_attempts=candidate_attempts,
                )
            return SourceProbeResult(
                source="openaq",
                status=SourceStatus.INELIGIBLE,
                message=(
                    f"OpenAQ returned 0 hourly PM2.5 rows for sensor {sensor_id} "
                    f"in the requested window {config.start_date} – {config.end_date}"
                ),
                observations=pd.DataFrame(),
                metadata=metadata,
            )

        df_raw = pd.DataFrame(all_rows)
        df_canonical, rejected_unit_count = apply_canonical_rules(df_raw)
        metadata["rejected_unit_rows"] = rejected_unit_count
        candidate_attempts.append(_candidate_attempt(loc, sensor, "selected"))

        return SourceProbeResult(
            source="openaq",
            status=SourceStatus.ELIGIBLE,
            message=(
                f"Downloaded {len(all_rows)} hourly rows for sensor {sensor_id} "
                f"({config.start_date} – {config.end_date}). "
                f"Verified metadata history: {verified_history_days:.1f} days."
            ),
            observations=df_canonical,
            metadata=metadata,
        )


def _parse_declared_timestamp(value: Any) -> Optional[datetime]:
    """Parse an OpenAQ datetime object/string as UTC, returning None if absent."""
    if isinstance(value, dict):
        value = value.get("utc")
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _rank_candidates(
    candidates: List[Dict[str, Any]], config: FeasibilityConfig
) -> List[Dict[str, Any]]:
    """Order viable-looking stations before stale/unknown-history API entries."""
    window_start = datetime.combine(config.start_date, datetime.min.time(), tzinfo=timezone.utc)
    window_end = datetime.combine(config.end_date, datetime.max.time(), tzinfo=timezone.utc)

    def key(candidate: Dict[str, Any]) -> tuple:
        loc = candidate["loc"]
        first = _parse_declared_timestamp(loc.get("datetimeFirst"))
        last = _parse_declared_timestamp(loc.get("datetimeLast"))
        overlaps = bool(first and last and first <= window_end and last >= window_start)
        history_seconds = (last - first).total_seconds() if first and last else 0.0
        latest_seconds = last.timestamp() if last else float("-inf")
        distance = loc.get("distance")
        try:
            distance_value = float(distance)
        except (TypeError, ValueError):
            distance_value = float("inf")
        sensor_id = str(candidate["sensor"].get("id", ""))
        return (0 if overlaps else 1, -history_seconds, -latest_seconds, distance_value, sensor_id)

    return sorted(candidates, key=key)


def _candidate_attempt(loc: Dict[str, Any], sensor: Dict[str, Any], outcome: str) -> Dict[str, Any]:
    """Create secret-free, JSON-serializable evidence for an attempted sensor."""
    return {
        "location_id": str(loc.get("id", "")),
        "sensor_id": str(sensor.get("id", "")),
        "location_name": loc.get("name"),
        "declared_first_utc": _parse_declared_timestamp(loc.get("datetimeFirst")).isoformat()
        if _parse_declared_timestamp(loc.get("datetimeFirst"))
        else None,
        "declared_last_utc": _parse_declared_timestamp(loc.get("datetimeLast")).isoformat()
        if _parse_declared_timestamp(loc.get("datetimeLast"))
        else None,
        "outcome": outcome,
    }


def _persist_raw_response(name: str, url: str, response: Any) -> None:
    """Persist a successful OpenAQ response and secret-free provenance atomically."""
    raw_dir = Path("data/raw/openaq")
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{name}.json"
    metadata_path = raw_dir / f"{name}.json.meta.json"

    content = getattr(response, "content", None)
    # ``requests.Response.content`` is the byte-for-byte response snapshot.
    # Do not invent a JSON serialization when it is unavailable: that would no
    # longer be a raw capture and can make mocked/test responses look real.
    if not isinstance(content, bytes):
        return

    headers = getattr(response, "headers", {}) or {}
    if not isinstance(headers, Mapping):
        headers = {}
    metadata = {
        "source_url": url.split("?", 1)[0],
        "retrieval_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "etag": headers.get("ETag") or headers.get("etag"),
        "last_modified": headers.get("Last-Modified") or headers.get("last-modified"),
    }

    temp_raw = None
    temp_metadata = None
    try:
        with tempfile.NamedTemporaryFile(dir=raw_dir, delete=False, suffix=".tmp") as handle:
            handle.write(content)
            temp_raw = handle.name

        with tempfile.NamedTemporaryFile(
            mode="w", dir=raw_dir, delete=False, suffix=".tmp", encoding="utf-8"
        ) as handle:
            import json
            json.dump(metadata, handle, indent=2)
            temp_metadata = handle.name
            
        os.replace(temp_raw, raw_path)
        temp_raw = None
        os.replace(temp_metadata, metadata_path)
        temp_metadata = None
    finally:
        if temp_raw and os.path.exists(temp_raw):
            os.remove(temp_raw)
        if temp_metadata and os.path.exists(temp_metadata):
            os.remove(temp_metadata)
