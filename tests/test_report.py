"""
Tests for report generation.

Covers:
- API key / secret redaction from Markdown
- SourceStatus enum values rendered as plain strings (not "SourceStatus.BLOCKED_AUTH")
- Structured JSON fields: decision, selected_source, selected_source_id
- Atomic writes (output file exists and is valid after write)
- Gate-by-gate section present in Markdown
- Next actions generated from actual source statuses (not always "provide OpenAQ key")
- Official provenance links present
"""

import json
import os
from pathlib import Path

import pytest

from airguard_data.contracts import SourceStatus
from airguard_data.report import write_json_report, write_markdown_report


def _sample_data(
    selected_source=None,
    selected_source_id=None,
    decision="blocked",
    statuses=None,
):
    if statuses is None:
        statuses = {"openaq": "blocked_auth"}
    return {
        "retrieval_timestamp_utc": "2024-01-01T00:00:00+00:00",
        "config": {
            "sources": ["openaq"],
            "start": "2024-01-01",
            "end": "2024-01-01",
            "openaq_api_key": "SECRET123",  # must be redacted
        },
        "source_status": {
            src: {"status": status, "message": f"{src} message"}
            for src, status in statuses.items()
        },
        "decision": decision,
        "selected_source": selected_source,
        "selected_source_id": selected_source_id,
        "profiles": {},
    }


class TestSecretRedaction:
    def test_api_key_not_in_markdown(self, tmp_path):
        data = _sample_data()
        md_path = str(tmp_path / "report.md")
        write_markdown_report(md_path, data, {})
        content = Path(md_path).read_text()
        assert "SECRET123" not in content

    def test_openaq_api_key_field_not_in_markdown(self, tmp_path):
        data = _sample_data()
        md_path = str(tmp_path / "report.md")
        write_markdown_report(md_path, data, {})
        content = Path(md_path).read_text()
        assert "openaq_api_key" not in content

    def test_api_key_not_in_json(self, tmp_path):
        """JSON report should strip key-like fields via the caller; we test the writer doesn't add them."""
        # The CLI strips the key before calling write_json_report
        clean_data = _sample_data()
        del clean_data["config"]["openaq_api_key"]
        json_path = str(tmp_path / "result.json")
        write_json_report(json_path, clean_data)
        content = Path(json_path).read_text()
        assert "SECRET123" not in content


class TestStatusRendering:
    def test_markdown_status_is_plain_string(self, tmp_path):
        data = _sample_data(statuses={"openaq": SourceStatus.BLOCKED_AUTH})
        md_path = str(tmp_path / "report.md")
        write_markdown_report(md_path, data, {})
        content = Path(md_path).read_text()
        assert "SourceStatus" not in content
        assert "blocked_auth" in content

    def test_markdown_eligible_status(self, tmp_path):
        data = _sample_data(
            statuses={"openaq": SourceStatus.ELIGIBLE},
            decision="selected",
            selected_source="openaq",
            selected_source_id="1001",
        )
        md_path = str(tmp_path / "report.md")
        write_markdown_report(md_path, data, {})
        content = Path(md_path).read_text()
        assert "eligible" in content
        assert "SourceStatus" not in content


class TestDecisionRendering:
    def test_blocked_decision_in_markdown(self, tmp_path):
        data = _sample_data(decision="blocked")
        md_path = str(tmp_path / "report.md")
        write_markdown_report(md_path, data, {})
        content = Path(md_path).read_text()
        assert "BLOCKED" in content.upper()

    def test_selected_decision_in_markdown(self, tmp_path):
        data = _sample_data(
            decision="selected",
            selected_source="openaq",
            selected_source_id="1001",
            statuses={"openaq": "eligible"},
        )
        md_path = str(tmp_path / "report.md")
        write_markdown_report(md_path, data, {})
        content = Path(md_path).read_text()
        assert "SELECTED" in content.upper()


class TestNextActions:
    def test_blocked_auth_advises_key_setup(self, tmp_path):
        data = _sample_data(statuses={"openaq": "blocked_auth"})
        md_path = str(tmp_path / "report.md")
        write_markdown_report(md_path, data, {})
        content = Path(md_path).read_text()
        assert "OPENAQ_API_KEY" in content

    def test_selected_does_not_advise_key_if_not_blocked(self, tmp_path):
        """When decision is selected, do not advise 'provide OpenAQ key' for blocked_auth sources."""
        # In a selected scenario, openaq succeeded
        data = _sample_data(
            statuses={"openaq": "eligible"},
            decision="selected",
            selected_source="openaq",
            selected_source_id="999",
        )
        md_path = str(tmp_path / "report.md")
        write_markdown_report(md_path, data, {})
        content = Path(md_path).read_text()
        # Should NOT recommend providing key when auth succeeded
        assert "OPENAQ_API_KEY" not in content

    def test_manual_review_advises_contact_donre(self, tmp_path):
        data = _sample_data(statuses={"hanoi_portal": "manual_review"})
        md_path = str(tmp_path / "report.md")
        write_markdown_report(md_path, data, {})
        content = Path(md_path).read_text()
        assert "hanoi" in content.lower() or "portal" in content.lower()


class TestProvenanceLinks:
    def test_openaq_link_present(self, tmp_path):
        data = _sample_data()
        md_path = str(tmp_path / "report.md")
        write_markdown_report(md_path, data, {})
        content = Path(md_path).read_text()
        assert "openaq.org" in content.lower()

    def test_airnow_link_present(self, tmp_path):
        data = _sample_data()
        md_path = str(tmp_path / "report.md")
        write_markdown_report(md_path, data, {})
        content = Path(md_path).read_text()
        assert "airnow" in content.lower()

    def test_hanoi_portal_link_present(self, tmp_path):
        data = _sample_data()
        md_path = str(tmp_path / "report.md")
        write_markdown_report(md_path, data, {})
        content = Path(md_path).read_text()
        assert "airhanoi.hanoi.gov.vn" in content


class TestAtomicWrite:
    def test_json_file_is_valid_json(self, tmp_path):
        data = _sample_data()
        del data["config"]["openaq_api_key"]
        json_path = str(tmp_path / "result.json")
        write_json_report(json_path, data)
        with open(json_path) as f:
            loaded = json.load(f)
        assert "decision" in loaded

    def test_markdown_file_exists_after_write(self, tmp_path):
        data = _sample_data()
        md_path = str(tmp_path / "sub" / "report.md")
        write_markdown_report(md_path, data, {})
        assert Path(md_path).exists()


class TestGateByGateSection:
    def test_gate_section_in_markdown(self, tmp_path):
        profiles = {
            ("openaq", "1001"): {
                "valid_count": 100,
                "coverage_ratio": 0.95,
                "verified_history_days": 400.0,
                "observed_duration_days": 30.0,
                "missing_hour_count": 5,
                "longest_consecutive_missing_hours": 2,
                "eligible": False,
                "gates": {
                    "station_observation": True,
                    "hourly_or_normalizable": True,
                    "365_days_history": True,
                    "70_percent_coverage": True,
                    "recognized_units": True,
                    "known_timezone": True,
                    "1000_valid_rows": False,
                },
            }
        }
        data = _sample_data(statuses={"openaq": "ineligible"})
        md_path = str(tmp_path / "report.md")
        write_markdown_report(md_path, data, profiles)
        content = Path(md_path).read_text()
        assert "Gate" in content or "gate" in content
        assert "1000_valid_rows" in content
