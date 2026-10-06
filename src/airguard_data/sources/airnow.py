"""
AirNow public hourly archive adapter.

Public file archive: https://files.airnowtech.org/airnow/{year}/{date}/HourlyData_{yyyymmddhh}.dat

File format (pipe-delimited, no header):
  date|time|site_id|site_name|gmt_offset|parameter|units|value|source

The file timestamp is GMT (UTC).  timestamp_local is derived by applying the
record's GMT offset to the UTC timestamp, resulting in a timezone-aware local
datetime.

Hanoi matching rules (conservative):
  - site_name must contain "hanoi" (case-insensitive), OR
  - site_name contains "vietnam" AND source contains "dos" or "department of state"
  A generic DOS row from another city must NOT match.

Raw files from live runs are persisted under data/raw/airnow/.  Each file gets
a sidecar .meta.json with secret-free provenance.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone, date as date_type
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from requests import Response

from ..contracts import SourceAdapter, SourceProbeResult, SourceStatus
from ..config import FeasibilityConfig
from ..http import HttpClient, RedactedException
from ..normalize import apply_canonical_rules

_ARCHIVE_BASE = "https://files.airnowtech.org/airnow"


def _build_hour_url(dt_utc: datetime) -> str:
    year = dt_utc.strftime("%Y")
    date_str = dt_utc.strftime("%Y%m%d")
    hr_str = dt_utc.strftime("%Y%m%d%H")
    return f"{_ARCHIVE_BASE}/{year}/{date_str}/HourlyData_{hr_str}.dat"


def _is_hanoi_row(site_name: str, source: str) -> bool:
    """
    Return True only when the record is clearly from Hanoi:
    - site_name contains "hanoi", OR
    - site_name contains "vietnam" AND source identifies DoS/Hanoi
    """
    name_lower = site_name.strip().lower()
    src_lower = source.strip().lower()

    if "hanoi" in name_lower:
        return True
    if "vietnam" in name_lower and (
        "dos" in src_lower
        or "department of state" in src_lower
        or "hanoi" in src_lower
    ):
        return True
    return False


def _parse_dat_line(
    line: str,
) -> Optional[Tuple[str, str, str, str, str, str, str, str, str]]:
    """
    Parse a single pipe-delimited AirNow dat record.
    Returns (date_val, time_val, site_id, site_name, gmt_offset, param, units, val, source)
    or None if the line is malformed.
    """
    parts = line.split("|")
    if len(parts) < 9:
        return None
    return tuple(p.strip() for p in parts[:9])  # type: ignore[return-value]


def _local_ts_from_gmt_offset(ts_utc: datetime, gmt_offset_str: str) -> datetime:
    """
    Apply the record's GMT offset to ts_utc.
    gmt_offset_str is an integer like "-7" or "+7".
    Returns a timezone-aware datetime with the appropriate fixed offset.
    """
    try:
        offset_hours = int(gmt_offset_str)
    except (ValueError, TypeError):
        offset_hours = 0
    tz = timezone(timedelta(hours=offset_hours))
    return ts_utc.astimezone(tz)


class AirNowAdapter(SourceAdapter):
    def probe(self, config: FeasibilityConfig) -> SourceProbeResult:
        client = HttpClient(config.timeout_seconds, config.offline_fixtures)

        # Build hour list (UTC, truncated to max)
        start_dt = datetime.combine(config.start_date, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = datetime.combine(config.end_date, datetime.max.time(), tzinfo=timezone.utc)

        hours: List[datetime] = []
        cur = start_dt
        while cur <= end_dt:
            hours.append(cur)
            cur += timedelta(hours=1)

        total_possible_hours = len(hours)
        hours = hours[: config.airnow_max_hours]
        capped = len(hours) < total_possible_hours

        all_rows: List[Dict[str, Any]] = []
        missing_archive_count = 0
        network_error_count = 0
        malformed_row_count = 0
        unrecognized_unit_count = 0
        skipped_non_hanoi_count = 0
        hours_returned: List[datetime] = []

        # Keep real raw snapshots in the project-level, git-ignored archive.
        # Fixture runs intentionally do not create raw snapshots.
        raw_dir: Optional[Path] = None
        if not config.offline_fixtures:
            raw_dir = Path("data/raw/airnow")

        for hr in hours:
            url = _build_hour_url(hr)

            try:
                resp = client.get(url)
            except RedactedException as exc:
                network_error_count += 1
                continue
            except FileNotFoundError:
                # Offline fixture missing – treat as 404
                missing_archive_count += 1
                continue

            if resp.status_code == 404:
                missing_archive_count += 1
                continue

            try:
                resp.raise_for_status()
            except Exception:
                network_error_count += 1
                continue

            raw_text = resp.text
            hours_returned.append(hr)

            # Persist raw file
            if raw_dir is not None:
                _persist_raw(raw_dir, url, hr, raw_text, resp)

            lines = raw_text.splitlines()
            for line in lines:
                if not line.strip():
                    continue
                parsed = _parse_dat_line(line)
                if parsed is None:
                    malformed_row_count += 1
                    continue

                date_val, time_val, site_id, site_name, gmt_offset, param, units, val, source = parsed

                if param.upper() != "PM2.5":
                    continue

                # Check Hanoi match BEFORE anything else (conservative)
                if not _is_hanoi_row(site_name, source):
                    skipped_non_hanoi_count += 1
                    continue

                # Parse GMT timestamp
                try:
                    # Format: MM/DD/YY|HH:MM
                    ts_utc = datetime.strptime(
                        f"{date_val} {time_val}", "%m/%d/%y %H:%M"
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    malformed_row_count += 1
                    continue

                ts_local = _local_ts_from_gmt_offset(ts_utc, gmt_offset)

                # Parse value safely
                try:
                    f_val: Optional[float] = float(val)
                except (ValueError, TypeError):
                    f_val = None

                all_rows.append(
                    {
                        "source": "airnow",
                        "provider": source,
                        "location_id": site_id,
                        "location_name": site_name,
                        # AirNow hourly .dat files do NOT include lat/lon
                        "latitude": None,
                        "longitude": None,
                        "timestamp_utc": ts_utc.isoformat(),
                        "timestamp_local": ts_local,
                        "pm2_5_ug_m3": f_val,
                        "unit_original": units,
                    }
                )

        df_raw = pd.DataFrame(all_rows) if all_rows else pd.DataFrame()
        if not df_raw.empty:
            df_canonical, rej_units = apply_canonical_rules(df_raw)
            unrecognized_unit_count = rej_units
        else:
            df_canonical = pd.DataFrame()
            unrecognized_unit_count = 0

        matched_count = len(all_rows)

        metadata: Dict[str, Any] = {
            "is_modeled": False,
            "verified_history_days": 0.0,  # cannot verify from capped sample
            "hours_requested": len(hours),
            "hours_capped": capped,
            "hours_returned": len(hours_returned),
            "hours_missing_archive": missing_archive_count,
            "hours_network_error": network_error_count,
            "rows_parsed": matched_count,
            "rows_malformed": malformed_row_count,
            "rows_unrecognized_unit": unrecognized_unit_count,
            "rows_skipped_non_hanoi": skipped_non_hanoi_count,
            "archive_base_url": _ARCHIVE_BASE,
        }

        status = (
            SourceStatus.BLOCKED_NETWORK
            if hours and network_error_count == len(hours)
            else SourceStatus.INELIGIBLE
        )
        if status is SourceStatus.BLOCKED_NETWORK:
            message = (
                f"All {len(hours)} requested AirNow archive hours failed with a "
                "network or HTTP access error; no availability conclusion can be drawn."
            )
        else:
            message = (
                f"Sampled {len(hours)} of {total_possible_hours} possible hours "
                f"({'capped' if capped else 'full range'}). "
                f"Archive hits: {len(hours_returned)}, "
                f"missing: {missing_archive_count}, "
                f"network/access errors: {network_error_count}, "
                f"Hanoi PM2.5 rows matched: {matched_count}. "
                "Cannot verify annual eligibility from a capped sample."
            )

        return SourceProbeResult(
            source="airnow",
            status=status,
            message=message,
            observations=df_canonical,
            metadata=metadata,
        )


def _persist_raw(
    raw_dir: Path,
    url: str,
    hr: datetime,
    raw_text: str,
    resp: Any,
) -> None:
    """Save raw text and a sidecar metadata JSON atomically."""
    # A snapshot is evidence only when it came from an actual HTTP response.
    # This avoids writing unit-test mocks as if they were source data.
    if not isinstance(resp, Response):
        return

    try:
        raw_dir.mkdir(parents=True, exist_ok=True)
        filename = f"HourlyData_{hr.strftime('%Y%m%d%H')}.dat"
        raw_path = raw_dir / filename
        meta_path = raw_dir / (filename + ".meta.json")

        sha256 = hashlib.sha256(raw_text.encode()).hexdigest()
        # Strip query params from URL before persisting
        safe_url = url.split("?")[0]
        headers = resp.headers
        meta = {
            "source_url": safe_url,
            "retrieval_timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "sha256": sha256,
            "etag": headers.get("ETag"),
            "last_modified": headers.get("Last-Modified"),
        }

        # Atomic write: temp file then replace
        with tempfile.NamedTemporaryFile(
            mode="w", dir=raw_dir, delete=False, suffix=".tmp"
        ) as tf:
            tf.write(raw_text)
            tmp_raw = tf.name
        os.replace(tmp_raw, raw_path)

        with tempfile.NamedTemporaryFile(
            mode="w", dir=raw_dir, delete=False, suffix=".tmp"
        ) as tf:
            json.dump(meta, tf, indent=2)
            tmp_meta = tf.name
        os.replace(tmp_meta, meta_path)

    except Exception:
        # Persistence failure is non-fatal
        pass
