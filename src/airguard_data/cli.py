"""
CLI for AirGuard Hanoi data feasibility probe.

Usage:
  airguard-data feasibility --start 2024-09-01 --end 2024-09-30 \\
      --output-dir artifacts/data_feasibility \\
      --report docs/data-source-audit.md

Also:
  python -m airguard_data.cli feasibility ...

Exit codes:
  0 : at least one eligible source was selected
  2 : command completed but decision is blocked / no eligible source
  1 : invalid CLI/configuration or unrecoverable internal error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from .config import FeasibilityConfig, OpenAQIngestionConfig, OPENAQ_MAX_RADIUS_M, SUPPORTED_SOURCES
from .contracts import SourceStatus
from .sources import OpenAQAdapter, AirNowAdapter, HanoiPortalAdapter
from .profile import profile_series
from .report import write_json_report, write_markdown_report
from .normalize import create_empty_canonical_df
from .ingest import ingest_openaq

# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="airguard-data",
        description="AirGuard Hanoi — data feasibility probe",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    feas = sub.add_parser("feasibility", help="Run the data feasibility gate")
    feas.add_argument(
        "--sources",
        default=",".join(_SOURCE_PRIORITY),
        help=f"Comma-separated source list. Supported: {sorted(SUPPORTED_SOURCES)}",
    )
    feas.add_argument("--start", required=True, metavar="YYYY-MM-DD")
    feas.add_argument("--end", required=True, metavar="YYYY-MM-DD")
    feas.add_argument("--latitude", type=float, default=21.0285)
    feas.add_argument("--longitude", type=float, default=105.8542)
    feas.add_argument(
        "--radius-m",
        type=int,
        default=25_000,
        help=f"Point-radius search radius in metres (max {OPENAQ_MAX_RADIUS_M})",
    )
    feas.add_argument("--timeout-seconds", type=int, default=30)
    feas.add_argument(
        "--airnow-max-hours",
        type=int,
        default=24,
        help="Maximum number of AirNow archive hours to request",
    )
    feas.add_argument("--output-dir", required=True)
    feas.add_argument("--report", required=True)
    feas.add_argument(
        "--offline-fixtures",
        default=None,
        metavar="PATH",
        help="Directory of fixture files for a deterministic offline end-to-end run",
    )

    ingest = sub.add_parser("ingest-openaq", help="Download historical OpenAQ hourly PM2.5 data")
    ingest.add_argument("--sensor-id", required=True, type=int)
    ingest.add_argument("--location-id", required=True)
    ingest.add_argument("--location-name", required=True)
    ingest.add_argument("--latitude", required=True, type=float)
    ingest.add_argument("--longitude", required=True, type=float)
    ingest.add_argument("--start", required=True, metavar="YYYY-MM-DD")
    ingest.add_argument("--end", required=True, metavar="YYYY-MM-DD")
    ingest.add_argument("--output-dir", required=True)
    ingest.add_argument("--raw-dir", default="data/raw/openaq")
    ingest.add_argument("--timeout-seconds", type=int, default=30)
    ingest.add_argument("--max-pages-per-month", type=int, default=100)
    ingest.add_argument("--offline-fixtures", default=None, metavar="PATH")

    return parser


# ---------------------------------------------------------------------------
# Core feasibility run (testable, returns exit code)
# ---------------------------------------------------------------------------

# Source priority order (from PLAN.md)
_SOURCE_PRIORITY = ["openaq", "airnow", "hanoi_portal"]

def run_feasibility(config: FeasibilityConfig) -> int:
    """
    Execute the feasibility gate.

    Returns:
        0 if at least one eligible source was selected
        2 if the decision is blocked
        1 on internal error (should not be reached if config is validated first)
    """
    adapters = {
        "openaq": OpenAQAdapter(),
        "airnow": AirNowAdapter(),
        "hanoi_portal": HanoiPortalAdapter(),
    }

    retrieval_timestamp = datetime.now(timezone.utc).isoformat()

    status_data: Dict[str, Any] = {}
    all_profiles: Dict[Tuple[str, str], Dict[str, Any]] = {}
    selected_source: Optional[str] = None
    selected_source_id: Optional[str] = None
    all_canonical_dfs = []

    # Probe in configured order (respects priority)
    for src_name in config.sources:
        if src_name not in adapters:
            # Should have been caught by validate() but guard anyway
            status_data[src_name] = {
                "status": "error",
                "message": f"Unknown source '{src_name}'",
            }
            continue

        adapter = adapters[src_name]
        try:
            res = adapter.probe(config)
        except Exception as exc:
            # Should never happen — adapters must not raise — but catch defensively
            status_data[src_name] = {
                "status": SourceStatus.ERROR.value,
                "message": "Unexpected adapter exception (internal error)",
            }
            continue

        status_data[src_name] = {
            "status": res.status,  # kept as SourceStatus enum; report renders it
            "message": res.message,
        }

        df = res.observations
        if df is None or (hasattr(df, "empty") and df.empty):
            continue

        for loc_id, group in df.groupby("location_id"):
            prof, dedup_df = profile_series(group, res.metadata)
            key = (src_name, str(loc_id))
            all_profiles[key] = prof
            all_canonical_dfs.append(dedup_df)

            # Selection: first (highest priority) source/location passing all gates
            if selected_source is None and prof.get("eligible"):
                selected_source = src_name
                selected_source_id = str(loc_id)

    # Combine canonical rows
    if all_canonical_dfs:
        final_df = pd.concat(all_canonical_dfs, ignore_index=True)
        final_df = final_df.sort_values(["source", "location_id", "timestamp_utc"])
    else:
        final_df = create_empty_canonical_df()

    # Write outputs
    os.makedirs(config.output_dir, exist_ok=True)
    sample_csv_path = os.path.join(config.output_dir, "normalized_sample.csv")
    _atomic_write_df_csv(final_df, sample_csv_path)

    decision = "selected" if selected_source else "blocked"

    report_data: Dict[str, Any] = {
        "retrieval_timestamp_utc": retrieval_timestamp,
        "config": {
            "sources": config.sources,
            "start": str(config.start_date),
            "end": str(config.end_date),
            "latitude": config.latitude,
            "longitude": config.longitude,
            "radius_m": config.radius_m,
            "timeout_seconds": config.timeout_seconds,
            "airnow_max_hours": config.airnow_max_hours,
        },
        "source_status": {
            src: {
                "status": info["status"].value if hasattr(info["status"], "value") else str(info["status"]),
                "message": info["message"],
            }
            for src, info in status_data.items()
        },
        "decision": decision,
        "selected_source": selected_source,
        "selected_source_id": selected_source_id,
        "profiles": {
            f"{k[0]}_{k[1]}": v for k, v in all_profiles.items()
        },
    }

    result_json_path = os.path.join(config.output_dir, "result.json")
    write_json_report(result_json_path, report_data)
    write_markdown_report(config.report_path, report_data, all_profiles)

    return 0 if selected_source else 2


def _atomic_write_df_csv(df: pd.DataFrame, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=p.parent, delete=False, suffix=".tmp"
    ) as tf:
        df.to_csv(tf, index=False)
        tmp = tf.name
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

def main() -> None:
    parser = get_parser()
    args = parser.parse_args()

    if args.command == "ingest-openaq":
        try:
            config = OpenAQIngestionConfig(
                sensor_id=args.sensor_id, location_id=args.location_id,
                location_name=args.location_name, latitude=args.latitude,
                longitude=args.longitude, start_date=datetime.strptime(args.start, "%Y-%m-%d").date(),
                end_date=datetime.strptime(args.end, "%Y-%m-%d").date(),
                output_dir=args.output_dir, raw_dir=args.raw_dir,
                timeout_seconds=args.timeout_seconds, max_pages_per_month=args.max_pages_per_month,
                offline_fixtures=args.offline_fixtures, openaq_api_key=os.environ.get("OPENAQ_API_KEY"),
            )
            code, result = ingest_openaq(config)
            if code:
                print(result.get("message", result.get("status", "ingestion failed")), file=sys.stderr)
            sys.exit(code)
        except Exception as exc:
            print(f"Configuration error: {exc}", file=sys.stderr)
            sys.exit(1)

    try:
        config = FeasibilityConfig.from_args(args)
        config.validate()
    except (ValueError, Exception) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    exit_code = run_feasibility(config)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
