"""
Tests for CLI behavior.

Covers:
- Exit code 0 when eligible source selected
- Exit code 2 when decision is blocked (all sources ineligible/blocked)
- Exit code 1 on invalid configuration
- Structured decision fields in JSON output
- Status rendered as string value (not SourceStatus.BLOCKED_AUTH)
- Invalid source name in --sources raises error (exit 1)
- run_feasibility() returns the correct int (testable without sys.exit)
- Offline end-to-end: generates JSON, CSV, and Markdown from fixture dir
"""

import json
import os
import sys
import pytest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from airguard_data.config import FeasibilityConfig
from airguard_data.cli import run_feasibility, get_parser
from airguard_data.contracts import SourceStatus, SourceProbeResult

FIXTURE_DIR_E2E = Path(__file__).parent / "fixtures" / "offline_e2e"


def _make_config(tmp_path, **overrides):
    defaults = dict(
        sources=["openaq", "airnow", "hanoi_portal"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 1),
        latitude=21.0285,
        longitude=105.8542,
        radius_m=25_000,
        timeout_seconds=5,
        airnow_max_hours=1,
        output_dir=str(tmp_path / "out"),
        report_path=str(tmp_path / "report.md"),
    )
    defaults.update(overrides)
    return FeasibilityConfig(**defaults)


class TestExitCodes:
    def test_exit_2_when_all_blocked(self, tmp_path):
        """All adapters return blocked/ineligible → exit code 2."""
        config = _make_config(tmp_path, sources=["openaq"], openaq_api_key=None)
        code = run_feasibility(config)
        assert code == 2

    def test_exit_code_1_from_parser_on_invalid_source(self):
        """Invalid source name should cause configuration error → exit 1."""
        config = FeasibilityConfig(
            sources=["not_a_real_source"],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 1),
            latitude=21.0,
            longitude=105.0,
            radius_m=25_000,
            timeout_seconds=5,
            airnow_max_hours=1,
            output_dir="/tmp/x",
            report_path="/tmp/x.md",
        )
        with pytest.raises(ValueError, match="Unknown source"):
            config.validate()

    def test_exit_0_when_eligible(self, tmp_path):
        """Mock an eligible result and confirm exit code is 0."""
        import pandas as pd
        from airguard_data.normalize import CANONICAL_COLUMNS
        from datetime import datetime, timezone, timedelta

        # Build a df with 1000 valid UTC-aware rows spanning ~42 days
        rows = []
        base_ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        for i in range(1000):
            ts = base_ts + timedelta(hours=i)
            rows.append({c: None for c in CANONICAL_COLUMNS})
            rows[-1].update({
                "source": "openaq",
                "provider": "test",
                "location_id": "1",
                "location_name": "Hanoi",
                "latitude": 21.0,
                "longitude": 105.0,
                # `ts` is already timezone-aware.  Passing `tz=` again is
                # rejected by pandas 3.x.
                "timestamp_utc": pd.Timestamp(ts),
                "timestamp_local": ts,
                "pm2_5_ug_m3": 15.0,
                "unit_original": "ug/m3",
                "is_valid": True,
            })
        eligible_df = pd.DataFrame(rows)

        eligible_result = SourceProbeResult(
            source="openaq",
            status=SourceStatus.ELIGIBLE,
            message="OK",
            observations=eligible_df,
            metadata={
                "is_modeled": False,
                "verified_history_days": 2000.0,
            },
        )

        config = _make_config(tmp_path, sources=["openaq"], openaq_api_key="testkey")
        with patch("airguard_data.cli.OpenAQAdapter.probe", return_value=eligible_result):
            code = run_feasibility(config)

        assert code == 0


class TestOutputFiles:
    def test_json_and_markdown_written_on_blocked(self, tmp_path):
        """Even on exit code 2, both files must be written."""
        config = _make_config(tmp_path, sources=["openaq"], openaq_api_key=None)
        run_feasibility(config)

        json_path = Path(config.output_dir) / "result.json"
        md_path = Path(config.report_path)
        assert json_path.exists(), "result.json must be written even on blocked"
        assert md_path.exists(), "report.md must be written even on blocked"

    def test_json_contains_structured_decision_fields(self, tmp_path):
        config = _make_config(tmp_path, sources=["openaq"], openaq_api_key=None)
        run_feasibility(config)

        json_path = Path(config.output_dir) / "result.json"
        with open(json_path) as f:
            data = json.load(f)

        assert "decision" in data
        assert data["decision"] in ("selected", "blocked")
        assert "selected_source" in data
        assert "selected_source_id" in data

    def test_status_in_json_is_string_not_enum_repr(self, tmp_path):
        config = _make_config(tmp_path, sources=["openaq"], openaq_api_key=None)
        run_feasibility(config)

        json_path = Path(config.output_dir) / "result.json"
        with open(json_path) as f:
            data = json.load(f)

        for src, info in data["source_status"].items():
            status_val = info["status"]
            # Must be a plain string like "blocked_auth", not "SourceStatus.BLOCKED_AUTH"
            assert "SourceStatus" not in status_val, (
                f"Status for {src} rendered as enum repr: {status_val}"
            )

    def test_markdown_status_is_string(self, tmp_path):
        config = _make_config(tmp_path, sources=["openaq"], openaq_api_key=None)
        run_feasibility(config)

        md_path = Path(config.report_path)
        content = md_path.read_text()
        assert "SourceStatus" not in content, "Enum repr leaked into Markdown"

    def test_csv_normalized_sample_written(self, tmp_path):
        config = _make_config(tmp_path, sources=["openaq"], openaq_api_key=None)
        run_feasibility(config)

        csv_path = Path(config.output_dir) / "normalized_sample.csv"
        assert csv_path.exists()


class TestOfflineE2E:
    def test_offline_e2e_generates_all_artifacts(self, tmp_path):
        """
        Full offline end-to-end: using fixture dir, all adapters run, JSON/CSV/MD written,
        exit code is 0 (OpenAQ fixture has verified_history_days > 365 and 1000 valid rows).
        """
        config = _make_config(
            tmp_path,
            sources=["openaq", "airnow", "hanoi_portal"],
            start_date=date(2024, 9, 1),
            end_date=date(2024, 9, 1),
            airnow_max_hours=1,
            offline_fixtures=str(FIXTURE_DIR_E2E),
            openaq_api_key=None,
        )

        code = run_feasibility(config)

        # JSON must exist
        json_path = Path(config.output_dir) / "result.json"
        assert json_path.exists()
        with open(json_path) as f:
            data = json.load(f)
        assert "decision" in data

        # Markdown must exist
        md_path = Path(config.report_path)
        assert md_path.exists()
        md_content = md_path.read_text()
        assert "Data Source Audit" in md_content

        # CSV must exist
        csv_path = Path(config.output_dir) / "normalized_sample.csv"
        assert csv_path.exists()

        # Exit code: OpenAQ with 1000 rows and >365 meta-days → 0
        assert code == 0

    def test_offline_unknown_url_raises_file_not_found(self, tmp_path):
        """Unknown fixture URL must fail clearly (FileNotFoundError), not return {}."""
        from airguard_data.http import HttpClient
        client = HttpClient(timeout_seconds=5, offline_fixtures_path=str(FIXTURE_DIR_E2E))
        with pytest.raises(FileNotFoundError):
            client.get("https://example.com/unknown/path/that/has/no/fixture")


class TestParserValidation:
    def test_radius_above_limit_raises_on_validate(self):
        config = FeasibilityConfig(
            sources=["openaq"],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 1),
            latitude=21.0,
            longitude=105.0,
            radius_m=26_000,  # > 25_000
            timeout_seconds=5,
            airnow_max_hours=1,
            output_dir="/tmp/x",
            report_path="/tmp/x.md",
        )
        with pytest.raises(ValueError, match="radius_m"):
            config.validate()
