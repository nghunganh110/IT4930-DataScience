from dataclasses import dataclass, field
from typing import Optional, List
from datetime import date
import os
import argparse

SUPPORTED_SOURCES = {"openaq", "airnow", "hanoi_portal"}
OPENAQ_MAX_RADIUS_M = 25_000


@dataclass
class FeasibilityConfig:
    sources: List[str]
    start_date: date
    end_date: date
    latitude: float
    longitude: float
    radius_m: int
    timeout_seconds: int
    airnow_max_hours: int
    output_dir: str
    report_path: str
    offline_fixtures: Optional[str] = None
    openaq_api_key: Optional[str] = None

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "FeasibilityConfig":
        return cls(
            sources=[s.strip() for s in args.sources.split(",")],
            start_date=date.fromisoformat(args.start),
            end_date=date.fromisoformat(args.end),
            latitude=args.latitude,
            longitude=args.longitude,
            radius_m=args.radius_m,
            timeout_seconds=args.timeout_seconds,
            airnow_max_hours=args.airnow_max_hours,
            output_dir=args.output_dir,
            report_path=args.report,
            offline_fixtures=args.offline_fixtures,
            openaq_api_key=os.environ.get("OPENAQ_API_KEY"),
        )

    def validate(self):
        """Validate config; raise ValueError with a clear message on any violation."""
        # Source names
        unknown = set(self.sources) - SUPPORTED_SOURCES
        if unknown:
            raise ValueError(
                f"Unknown source(s): {sorted(unknown)}. Supported: {sorted(SUPPORTED_SOURCES)}"
            )
        if not self.sources:
            raise ValueError("At least one source must be specified")

        # Date range
        if self.start_date > self.end_date:
            raise ValueError("start_date must be before or equal to end_date")

        # Geo
        if not (-90 <= self.latitude <= 90):
            raise ValueError(f"latitude must be in [-90, 90], got {self.latitude}")
        if not (-180 <= self.longitude <= 180):
            raise ValueError(f"longitude must be in [-180, 180], got {self.longitude}")

        # Radius
        if self.radius_m <= 0:
            raise ValueError(f"radius_m must be positive, got {self.radius_m}")
        if self.radius_m > OPENAQ_MAX_RADIUS_M:
            raise ValueError(
                f"radius_m must be at most {OPENAQ_MAX_RADIUS_M} (OpenAQ point-radius limit),"
                f" got {self.radius_m}"
            )

        # Timeout
        if self.timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be positive, got {self.timeout_seconds}")

        # AirNow cap
        if self.airnow_max_hours <= 0:
            raise ValueError(f"airnow_max_hours must be positive, got {self.airnow_max_hours}")


@dataclass
class OpenAQIngestionConfig:
    sensor_id: int
    location_id: str
    location_name: str
    latitude: float
    longitude: float
    start_date: date
    end_date: date
    output_dir: str
    raw_dir: str = "data/raw/openaq"
    timeout_seconds: int = 30
    max_pages_per_month: int = 100
    offline_fixtures: Optional[str] = None
    openaq_api_key: Optional[str] = None

    def validate(self) -> None:
        if self.sensor_id <= 0:
            raise ValueError("sensor_id must be positive")
        if not self.location_id.strip() or not self.location_name.strip():
            raise ValueError("location_id and location_name must be non-empty")
        if self.start_date > self.end_date:
            raise ValueError("start_date must be before or equal to end_date")
        if not (-90 <= self.latitude <= 90) or not (-180 <= self.longitude <= 180):
            raise ValueError("latitude/longitude are out of range")
        if self.timeout_seconds <= 0 or self.max_pages_per_month <= 0:
            raise ValueError("timeout_seconds and max_pages_per_month must be positive")
        if not self.output_dir.strip() or not self.raw_dir.strip():
            raise ValueError("output_dir and raw_dir must be non-empty")


@dataclass
class WeatherIngestionConfig:
    latitude: float
    longitude: float
    start_date: date
    end_date: date
    output_dir: str
    raw_dir: str = "data/raw/open_meteo"
    timeout_seconds: int = 30
    offline_fixtures: Optional[str] = None

    def validate(self) -> None:
        if not (-90 <= self.latitude <= 90) or not (-180 <= self.longitude <= 180):
            raise ValueError("latitude/longitude are out of range")
        if self.start_date > self.end_date:
            raise ValueError("start_date must be before or equal to end_date")
        if not self.output_dir.strip() or not self.raw_dir.strip():
            raise ValueError("output_dir and raw_dir must be non-empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
