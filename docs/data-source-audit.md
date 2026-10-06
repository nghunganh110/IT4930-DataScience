# Data Source Audit Report — AirGuard Hanoi

**Retrieval timestamp (UTC):** 2026-10-06T02:40:31.890864+00:00

## Configuration

```json
{
  "sources": [
    "openaq"
  ],
  "start": "2024-01-01",
  "end": "2024-03-01",
  "latitude": 21.0285,
  "longitude": 105.8542,
  "radius_m": 25000,
  "timeout_seconds": 15,
  "airnow_max_hours": 24
}
```

## Source Status

| Source | Status | Message |
|---|---|---|
| openaq | eligible | Downloaded 1372 hourly rows for sensor 21632 (2024-01-01 – 2024-03-01). Verified metadata history: 3072.9 days. |

## Per-Station Quality

| Source | Location | Valid Rows | Coverage | Verified Days (meta) | Missing Hours | Longest Gap (h) | Eligible |
|---|---|---|---|---|---|---|---|
| openaq | 7441 | 1372 | 97.86% | 3072.9 | 30 | 18 | ✓ |

## Gate-by-Gate Results

### openaq / 7441
- ✓ `station_observation`
- ✓ `hourly_or_normalizable`
- ✓ `365_days_history`
- ✓ `70_percent_coverage`
- ✓ `recognized_units`
- ✓ `known_timezone`
- ✓ `1000_valid_rows`

Downloaded: 58.4 days, 1372 valid rows. Metadata-verified history: 3072.9 days.

## Decision

**Decision: SELECTED**  
Source: `openaq` / Location: `7441`

## Limitations

- AirNow public archive samples are capped; annual eligibility cannot be inferred from a partial sample.
- OpenAQ downloaded window may be much shorter than metadata-verified history; these are reported separately above.
- Open-Meteo / CAMS air-quality reanalysis data is modelled grid output and is **not** eligible as a target variable (it may be used as a future covariate).
- The Hanoi portal is only a manual-review candidate when reachable; no scraping of undocumented endpoints was performed.

## Next Actions

- **Proceed** with the selected source to build the ingestion pipeline.

## Provenance

- **OpenAQ v3 API**: <https://docs.openaq.org/reference/> — open-source air quality data aggregator
- **AirNow public archive**: <https://files.airnowtech.org/> / <https://www.airnow.gov/international/us-embassies-and-consulates/> — U.S. DoS embassy PM2.5 measurements
- **Hanoi Environmental Portal**: <https://airhanoi.hanoi.gov.vn/> — Hanoi Department of Natural Resources and Environment

