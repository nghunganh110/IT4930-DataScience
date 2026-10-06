"""
Hanoi Environmental Monitoring Portal adapter.

Official portal: https://airhanoi.hanoi.gov.vn/

This adapter is a safe metadata/manual-review adapter only.
It MAY validate reachability of the documented landing page but MUST NOT
scrape undocumented/JS-rendered/private endpoints.

Status taxonomy:
  - manual_review  : portal is reachable; human must find and document
                     the official API/export endpoint
  - blocked_network: portal is unreachable (transport/timeout error)

A reachable 4xx/5xx is treated as blocked_network since no data endpoint
is accessible.
"""

from __future__ import annotations

import pandas as pd

from ..contracts import SourceAdapter, SourceProbeResult, SourceStatus
from ..config import FeasibilityConfig
from ..http import HttpClient, RedactedException

PORTAL_URL = "https://airhanoi.hanoi.gov.vn/"
PORTAL_DOCS = "https://airhanoi.hanoi.gov.vn/"

MANUAL_REVIEW_MSG = (
    "Portal landing page is reachable. "
    "A documented public API or bulk-export endpoint has not been identified. "
    "Recommended action: contact the Hanoi DONRE (Department of Natural Resources "
    "and Environment) to request official PM2.5 data access or review "
    f"the portal at {PORTAL_URL} for any documented data-download links."
)


class HanoiPortalAdapter(SourceAdapter):
    def probe(self, config: FeasibilityConfig) -> SourceProbeResult:
        client = HttpClient(config.timeout_seconds, config.offline_fixtures)

        try:
            resp = client.get(PORTAL_URL)
            sc = getattr(resp, "status_code", 200)

            if sc < 400:
                return SourceProbeResult(
                    source="hanoi_portal",
                    status=SourceStatus.MANUAL_REVIEW,
                    message=MANUAL_REVIEW_MSG,
                    observations=pd.DataFrame(),
                    metadata={
                        "url": PORTAL_URL,
                        "http_status": sc,
                        "is_modeled": False,
                        "recommended_action": (
                            "Contact Hanoi DONRE for official PM2.5 export API."
                        ),
                    },
                )
            else:
                return SourceProbeResult(
                    source="hanoi_portal",
                    status=SourceStatus.BLOCKED_NETWORK,
                    message=f"Portal returned HTTP {sc}; cannot assess data availability.",
                    observations=pd.DataFrame(),
                    metadata={"url": PORTAL_URL, "http_status": sc, "is_modeled": False},
                )

        except (RedactedException, FileNotFoundError) as exc:
            return SourceProbeResult(
                source="hanoi_portal",
                status=SourceStatus.BLOCKED_NETWORK,
                message=f"Portal unreachable: {exc}",
                observations=pd.DataFrame(),
                metadata={"url": PORTAL_URL, "is_modeled": False},
            )
        except Exception as exc:
            return SourceProbeResult(
                source="hanoi_portal",
                status=SourceStatus.BLOCKED_NETWORK,
                message=f"Portal unreachable: {type(exc).__name__}",
                observations=pd.DataFrame(),
                metadata={"url": PORTAL_URL, "is_modeled": False},
            )
