"""
Shared HTTP client with timeout, bounded retries, secret-safe diagnostics,
and optional offline-fixture mode.

Offline fixture mode:
  Pass `offline_fixtures_path` pointing to a directory.  For each GET request
  the client resolves a fixture file by normalising the URL path and trying:

    <offline_fixtures_path>/<normalised_path>.json
    <offline_fixtures_path>/<normalised_path>.dat
    <offline_fixtures_path>/<normalised_path>

  A "normalised path" strips leading slashes, replaces remaining slashes with
  underscores, and appends any relevant filename from the URL path.

  Unknown URLs raise FileNotFoundError so callers fail clearly rather than
  silently returning empty data.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _redact_url(url: str) -> str:
    """Strip query-string from URL to avoid leaking API keys."""
    return url.split("?")[0] + "?[redacted]" if "?" in url else url


# ---------------------------------------------------------------------------
# MockResponse used in offline mode
# ---------------------------------------------------------------------------

class _MockResponse:
    """Minimal requests.Response-like object backed by fixture data."""

    def __init__(self, body: bytes, status_code: int = 200, content_type: str = "text/plain"):
        self._body = body
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        # Expose etag / last-modified when present in a sidecar .meta.json
        self.etag: Optional[str] = None
        self.last_modified: Optional[str] = None

    @property
    def text(self) -> str:
        return self._body.decode("utf-8", errors="replace")

    @property
    def content(self) -> bytes:
        return self._body

    def json(self):
        return json.loads(self._body)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"HTTP {self.status_code}", response=self  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# RedactedException
# ---------------------------------------------------------------------------

class RedactedException(Exception):
    """Transport/HTTP exception with secrets stripped from the message."""


# ---------------------------------------------------------------------------
# HttpClient
# ---------------------------------------------------------------------------

class HttpClient:
    """
    Thin wrapper around requests.Session with:
    - User-Agent header
    - Bounded retries for 429/5xx
    - Secret-safe exception messages (query params stripped)
    - Optional offline-fixture mode (no network)
    """

    def __init__(
        self,
        timeout_seconds: int,
        offline_fixtures_path: Optional[str] = None,
    ):
        self.timeout = timeout_seconds
        self.offline_fixtures_path = offline_fixtures_path

        # Real session (used only when not in offline mode)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "AirGuard-Data-Feasibility-Probe/1.0"

        retries = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get(self, url: str, **kwargs) -> requests.Response | _MockResponse:
        if self.offline_fixtures_path:
            return self._get_offline(url)
        return self._get_live(url, **kwargs)

    # ------------------------------------------------------------------
    # Offline mode
    # ------------------------------------------------------------------

    def _get_offline(self, url: str) -> _MockResponse:
        fixture_dir = Path(self.offline_fixtures_path)
        candidates = self._offline_candidates(url, fixture_dir)

        for path in candidates:
            if path.exists():
                body = path.read_bytes()
                # Determine content-type from extension
                if path.suffix == ".json":
                    ct = "application/json"
                elif path.suffix == ".dat":
                    ct = "text/plain"
                else:
                    ct = "text/plain"

                resp = _MockResponse(body, status_code=200, content_type=ct)

                # Load optional sidecar metadata
                meta_path = path.with_suffix(".meta.json")
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text())
                        resp.status_code = meta.get("status_code", 200)
                        resp.etag = meta.get("etag")
                        resp.last_modified = meta.get("last_modified")
                    except Exception:
                        pass

                return resp

        raise FileNotFoundError(
            f"Offline fixture not found for URL: {_redact_url(url)}\n"
            f"Looked in: {fixture_dir}\n"
            f"Tried names: {[c.name for c in candidates]}"
        )

    def _offline_candidates(self, url: str, fixture_dir: Path) -> list[Path]:
        """
        Derive candidate fixture file paths from a URL.
        Strategy: use the last non-empty path segment as the base filename,
        plus a slugified full-path fallback.
        """
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.split("/") if p]

        candidates: list[Path] = []

        if path_parts:
            # Most-specific: just the last segment (e.g. "HourlyData_2024010100.dat")
            last = path_parts[-1]
            for ext in ("", ".json", ".dat"):
                candidates.append(fixture_dir / (last + ext))

            # Slug of full path e.g. "v3_sensors_123_hours.json"
            slug = "_".join(path_parts)
            for ext in (".json", ".dat", ""):
                candidates.append(fixture_dir / (slug + ext))

        return candidates

    # ------------------------------------------------------------------
    # Live mode
    # ------------------------------------------------------------------

    def _get_live(self, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        try:
            resp = self.session.get(url, **kwargs)
            return resp
        except requests.exceptions.ConnectionError as exc:
            raise RedactedException(
                f"Connection error to {_redact_url(url)}: {type(exc).__name__}"
            ) from None
        except requests.exceptions.Timeout as exc:
            raise RedactedException(
                f"Timeout connecting to {_redact_url(url)}"
            ) from None
        except requests.exceptions.RequestException as exc:
            # Strip any URL (may contain API key in query string)
            safe_msg = re.sub(r"https?://\S+", lambda m: _redact_url(m.group()), str(exc))
            raise RedactedException(safe_msg) from None

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def sha256_of_text(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    @staticmethod
    def sha256_of_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()
