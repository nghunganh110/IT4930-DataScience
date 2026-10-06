"""
Reporting: JSON result and Markdown audit report generation.

All writes are atomic (temp file then os.replace).
API keys and other secrets must never appear in any output.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Atomic write helpers
# ---------------------------------------------------------------------------

def _atomic_write_text(path: str, content: str) -> None:
    """Write content to path atomically via a temp file in the same directory."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=p.parent, delete=False, suffix=".tmp", encoding="utf-8"
    ) as tf:
        tf.write(content)
        tmp = tf.name
    os.replace(tmp, path)


def _atomic_write_json(path: str, data: Any) -> None:
    _atomic_write_text(path, json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# JSON report
# ---------------------------------------------------------------------------

def write_json_report(path: str, data: Dict[str, Any]) -> None:
    _atomic_write_json(path, data)


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------

def _status_str(status_val: Any) -> str:
    """Render a SourceStatus value as its string value (not the enum repr)."""
    if hasattr(status_val, "value"):
        return str(status_val.value)
    return str(status_val)


def write_markdown_report(
    path: str,
    data: Dict[str, Any],
    profiles: Dict[Any, Dict[str, Any]],
) -> None:
    lines = []

    lines.append("# Data Source Audit Report — AirGuard Hanoi")
    lines.append("")
    lines.append(f"**Retrieval timestamp (UTC):** {data.get('retrieval_timestamp_utc', 'unknown')}")
    lines.append("")

    # ── Configuration ─────────────────────────────────────────────────────
    lines.append("## Configuration")
    lines.append("")
    cfg = data.get("config", {})
    safe_cfg = {k: v for k, v in cfg.items() if "key" not in k.lower() and "secret" not in k.lower()}
    lines.append("```json")
    lines.append(json.dumps(safe_cfg, indent=2))
    lines.append("```")
    lines.append("")

    # ── Source status table ────────────────────────────────────────────────
    lines.append("## Source Status")
    lines.append("")
    lines.append("| Source | Status | Message |")
    lines.append("|---|---|---|")
    for src, info in data.get("source_status", {}).items():
        status_display = _status_str(info.get("status", "unknown"))
        msg = str(info.get("message", "")).replace("|", "\\|")
        lines.append(f"| {src} | {status_display} | {msg} |")
    lines.append("")

    # ── Per-station quality table ──────────────────────────────────────────
    lines.append("## Per-Station Quality")
    lines.append("")
    lines.append("| Source | Location | Valid Rows | Coverage | Verified Days (meta) | Missing Hours | Longest Gap (h) | Eligible |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for key, p in profiles.items():
        src, loc = key if isinstance(key, tuple) else (str(key).split("_", 1)[0], str(key))
        eligible = p.get("eligible", False)
        lines.append(
            f"| {src} | {loc} "
            f"| {p.get('valid_count', 0)} "
            f"| {p.get('coverage_ratio', 0.0):.2%} "
            f"| {p.get('verified_history_days', 0.0):.1f} "
            f"| {p.get('missing_hour_count', 0)} "
            f"| {p.get('longest_consecutive_missing_hours', 0)} "
            f"| {'✓' if eligible else '✗'} |"
        )
    if not profiles:
        lines.append("| — | — | — | — | — | — | — | — |")
    lines.append("")

    # ── Gate-by-gate decisions ────────────────────────────────────────────
    lines.append("## Gate-by-Gate Results")
    lines.append("")
    if profiles:
        for key, p in profiles.items():
            src, loc = key if isinstance(key, tuple) else (str(key).split("_", 1)[0], str(key))
            lines.append(f"### {src} / {loc}")
            gates = p.get("gates", {})
            for gate_name, passed in gates.items():
                mark = "✓" if passed else "✗"
                lines.append(f"- {mark} `{gate_name}`")
            lines.append(
                f"\nDownloaded: {p.get('observed_duration_days', 0.0):.1f} days, "
                f"{p.get('valid_count', 0)} valid rows. "
                f"Metadata-verified history: {p.get('verified_history_days', 0.0):.1f} days."
            )
            lines.append("")
    else:
        lines.append("No profiled series (no non-empty observations).")
        lines.append("")

    # ── Decision ──────────────────────────────────────────────────────────
    lines.append("## Decision")
    lines.append("")
    decision = data.get("decision", "blocked")
    selected = data.get("selected_source_id")
    selected_src = data.get("selected_source")
    if decision == "selected" and selected:
        lines.append(f"**Decision: SELECTED**  \nSource: `{selected_src}` / Location: `{selected}`")
    else:
        lines.append("**Decision: BLOCKED** — No source passed all eligibility gates.")
    lines.append("")

    # ── Limitations ───────────────────────────────────────────────────────
    lines.append("## Limitations")
    lines.append("")
    lines.append(
        "- AirNow public archive samples are capped; annual eligibility "
        "cannot be inferred from a partial sample."
    )
    lines.append(
        "- OpenAQ downloaded window may be much shorter than metadata-verified history; "
        "these are reported separately above."
    )
    lines.append(
        "- Open-Meteo / CAMS air-quality reanalysis data is modelled grid output and "
        "is **not** eligible as a target variable (it may be used as a future covariate)."
    )
    lines.append(
        "- The Hanoi portal is only a manual-review candidate when reachable; "
        "no scraping of undocumented endpoints was performed."
    )
    lines.append("")

    # ── Next actions (generated from statuses) ────────────────────────────
    lines.append("## Next Actions")
    lines.append("")
    source_statuses = {
        src: _status_str(info.get("status", ""))
        for src, info in data.get("source_status", {}).items()
    }
    actions_added = False

    if source_statuses.get("openaq") == "blocked_auth":
        lines.append(
            "1. **OpenAQ** — Set the `OPENAQ_API_KEY` environment variable and re-run."
        )
        actions_added = True
    if source_statuses.get("openaq") == "blocked_network":
        lines.append(
            "1. **OpenAQ** — Check network connectivity or increase `--timeout-seconds`."
        )
        actions_added = True
    if source_statuses.get("airnow") in ("ineligible",):
        lines.append(
            "2. **AirNow** — Increase `--airnow-max-hours` and extend the date window "
            "to probe a larger sample. Note: historical eligibility requires official "
            "confirmed data coverage > 365 days."
        )
        actions_added = True
    if source_statuses.get("airnow") == "blocked_network":
        lines.append(
            "2. **AirNow** — The public archive could not be reached or returned an "
            "access error. Verify network access and the official archive URL, then re-run."
        )
        actions_added = True
    if source_statuses.get("hanoi_portal") in ("manual_review",):
        lines.append(
            "3. **Hanoi Portal** — Contact the Hanoi DONRE to request documented API "
            "or bulk PM2.5 export access at https://airhanoi.hanoi.gov.vn/"
        )
        actions_added = True
    if source_statuses.get("hanoi_portal") in ("blocked_network",):
        lines.append(
            "3. **Hanoi Portal** — The portal was unreachable. Verify the URL and "
            "network access, then re-run."
        )
        actions_added = True

    if decision == "selected":
        lines.append(
            "- **Proceed** with the selected source to build the ingestion pipeline."
        )
        actions_added = True

    if not actions_added:
        lines.append("- Review individual source statuses above.")

    lines.append("")

    # ── Provenance ────────────────────────────────────────────────────────
    lines.append("## Provenance")
    lines.append("")
    lines.append(
        "- **OpenAQ v3 API**: <https://docs.openaq.org/reference/> — "
        "open-source air quality data aggregator"
    )
    lines.append(
        "- **AirNow public archive**: "
        "<https://files.airnowtech.org/> / "
        "<https://www.airnow.gov/international/us-embassies-and-consulates/> — "
        "U.S. DoS embassy PM2.5 measurements"
    )
    lines.append(
        "- **Hanoi Environmental Portal**: "
        "<https://airhanoi.hanoi.gov.vn/> — "
        "Hanoi Department of Natural Resources and Environment"
    )
    lines.append("")

    _atomic_write_text(path, "\n".join(lines) + "\n")
