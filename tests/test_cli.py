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


class TestEDA_CLI:
    def test_eda_pm25_parser(self):
        parser = get_parser()

        # Test default gap_hours
        args = parser.parse_args([
            "eda-pm25",
            "--input", "in.csv",
            "--output-dir", "out",
            "--modeling-output", "mod.csv"
        ])
        assert args.command == "eda-pm25"
        assert args.input == "in.csv"
        assert args.output_dir == "out"
        assert args.modeling_output == "mod.csv"
        assert args.gap_hours == 24

        # Test explicit gap_hours
        args2 = parser.parse_args([
            "eda-pm25",
            "--input", "in.csv",
            "--output-dir", "out",
            "--modeling-output", "mod.csv",
            "--gap-hours", "48"
        ])
        assert args2.gap_hours == 48

class TestWeatherCLI:
    def test_ingest_weather_parser(self):
        parser = get_parser()
        args = parser.parse_args([
            "ingest-weather",
            "--latitude", "21.0",
            "--longitude", "105.8",
            "--start", "2024-01-01",
            "--end", "2024-01-02",
            "--output-dir", "out_dir",
            "--offline-fixtures", "fix_dir"
        ])
        assert args.command == "ingest-weather"
        assert args.latitude == 21.0
        assert args.longitude == 105.8
        assert args.start == "2024-01-01"
        assert args.end == "2024-01-02"
        assert args.output_dir == "out_dir"
        assert args.raw_dir == "data/raw/open_meteo"  # default
        assert args.timeout_seconds == 30  # default
        assert args.offline_fixtures == "fix_dir"

        args2 = parser.parse_args([
            "ingest-weather",
            "--latitude", "21.0",
            "--longitude", "105.8",
            "--start", "2024-01-01",
            "--end", "2024-01-02",
            "--output-dir", "out_dir",
            "--raw-dir", "custom_raw",
            "--timeout-seconds", "15"
        ])
        assert args2.raw_dir == "custom_raw"
        assert args2.timeout_seconds == 15

    def test_merge_pm25_weather_parser(self):
        parser = get_parser()
        args = parser.parse_args([
            "merge-pm25-weather",
            "--pm25-input", "pm25.csv",
            "--weather-input", "weather.csv",
            "--output", "merged.csv",
            "--report-dir", "reports"
        ])
        assert args.command == "merge-pm25-weather"
        assert args.pm25_input == "pm25.csv"
        assert args.weather_input == "weather.csv"
        assert args.output == "merged.csv"
        assert args.report_dir == "reports"

    def test_build_features_parser(self):
        parser = get_parser()
        args = parser.parse_args([
            "build-features",
            "--input", "in.csv",
            "--output", "out.csv",
            "--report-dir", "reports"
        ])
        assert args.command == "build-features"
        assert args.input == "in.csv"
        assert args.output == "out.csv"
        assert args.report_dir == "reports"
        assert args.horizons == "1,6,12,24"
        assert args.lags == "1,2,3,6,12,24,48,72,168"
        assert args.rolling_windows == "3,6,12,24,168"
        assert args.local_timezone == "Asia/Ho_Chi_Minh"

    @patch("sys.argv", new=["airguard-data", "ingest-weather"])
    def test_weather_integration_cli_e2e(self, tmp_path):
        from airguard_data.cli import main
        import json
        import pandas as pd

        # 1. Setup mock fixture for weather
        fixtures_dir = Path(__file__).parent / "fixtures" / "weather"

        # 2. Setup mock pm25 input
        pm25_path = tmp_path / "pm25.csv"
        pm25_df = pd.DataFrame({
            "timestamp_utc": ["2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z"],
            "pm2_5_ug_m3": [10.0, 15.0],
            "is_valid": [True, True]
        })
        pm25_df.to_csv(pm25_path, index=False)

        out_wx = tmp_path / "out_wx"
        out_merged = tmp_path / "merged.csv"
        out_reports = tmp_path / "reports"

        # Run ingest-weather
        with patch("sys.argv", [
            "airguard-data", "ingest-weather",
            "--latitude", "21.0", "--longitude", "105.8",
            "--start", "2024-01-01", "--end", "2024-01-01",
            "--output-dir", str(out_wx),
            "--offline-fixtures", str(fixtures_dir)
        ]):
            with pytest.raises(SystemExit) as e:
                main()
            assert e.value.code == 0

        wx_csv = out_wx / "hourly_weather.csv"
        assert wx_csv.exists()
        assert (out_wx / "weather_manifest.json").exists()
        assert (out_wx / "weather_quality_report.json").exists()

        # Run merge-pm25-weather
        with patch("sys.argv", [
            "airguard-data", "merge-pm25-weather",
            "--pm25-input", str(pm25_path),
            "--weather-input", str(wx_csv),
            "--output", str(out_merged),
            "--report-dir", str(out_reports)
        ]):
            with pytest.raises(SystemExit) as e:
                main()
            assert e.value.code == 0

        merged_csv_content = pd.read_csv(out_merged)
        assert len(merged_csv_content) == 2
        assert "weather_available" in merged_csv_content.columns
        assert (out_reports / "merge_report.json").exists()
        assert (out_reports / "merge_report.md").exists()
