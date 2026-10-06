from typing import Protocol, Optional
from dataclasses import dataclass
import pandas as pd
from enum import Enum
from .config import FeasibilityConfig

class SourceStatus(str, Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    BLOCKED_AUTH = "blocked_auth"
    BLOCKED_NETWORK = "blocked_network"
    MANUAL_REVIEW = "manual_review"
    ERROR = "error"

@dataclass
class SourceProbeResult:
    source: str
    status: SourceStatus
    message: str
    observations: pd.DataFrame
    metadata: dict

class SourceAdapter(Protocol):
    def probe(self, config: FeasibilityConfig) -> SourceProbeResult:
        ...
