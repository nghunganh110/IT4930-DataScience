# AirGuard Hanoi — Data Feasibility Gate

This repository starts the AirGuard Hanoi data-science project from scratch.
Its first deliverable is a reproducible gate that determines whether a
ground-observed, hourly PM2.5 target can be used for forecasting. It does not
silently substitute modelled CAMS/Open-Meteo values for station observations.

## What this gate does

- probes OpenAQ v3 (requires `OPENAQ_API_KEY`), the public AirNow archive, and
  the Hanoi environmental portal;
- normalizes observations into one documented hourly PM2.5 contract;
- profiles coverage, gaps, units, timestamps, and history metadata;
- selects a source only when it meets the project’s eligibility gates; and
- writes a JSON result, normalized CSV, and Markdown audit report.

Read [the implementation plan](docs/ai/PLAN.md) for the full target-data
policy, eligibility gates, and architecture. The project survey and suggested
end-to-end scope are in [docs/project-survey.md](docs/project-survey.md).

## Run the feasibility gate

Install the package and test dependency in your chosen environment:

```bash
python -m pip install -e '.[test]'
```

Run a live, intentionally small probe:

```bash
export OPENAQ_API_KEY='your-key'  # optional, enables OpenAQ probing
airguard-data feasibility \
  --start 2024-01-01 --end 2024-01-01 \
  --airnow-max-hours 24 \
  --output-dir artifacts/data_feasibility \
  --report docs/data-source-audit.md
```

Exit code `0` means a target passed every gate; `2` means the gate completed
correctly but no eligible source was found; `1` means invalid configuration or
an internal failure. Raw live snapshots are stored locally under `data/raw/`
and are ignored by Git. Never place API keys in files, commands committed to
Git, or reports.

## Offline verification

The test suite makes no network requests:

```bash
python -m pytest -q
python -m airguard_data.cli feasibility \
  --start 2024-09-01 --end 2024-09-01 \
  --airnow-max-hours 1 \
  --offline-fixtures tests/fixtures/offline_e2e \
  --output-dir /tmp/airguard-e2e \
  --report /tmp/airguard-e2e-audit.md
```

The fixture run is an implementation check, not evidence that a live data
source is eligible. See [data/README.md](data/README.md) for data handling.

## Download historical OpenAQ data

After feasibility has selected a sensor, download a bounded range in monthly
chunks. The command reads `OPENAQ_API_KEY` only from the environment:

```bash
airguard-data ingest-openaq \
  --sensor-id 21632 --location-id 7441 --location-name Hanoi \
  --latitude 21.021939 --longitude 105.818806 \
  --start 2017-01-01 --end 2025-04-09 \
  --output-dir data/processed/openaq_sensor_21632
```

It writes `hourly_pm25.csv`, a manifest, and JSON/Markdown quality reports.
Generated raw and processed data stay outside Git.

## Run PM2.5 EDA and Dataset Preparation

After downloading historical data, run the EDA to generate charts, reports, and the segmentation modeling dataset:

```bash
airguard-data eda-pm25 \
  --input data/processed/openaq_sensor_21632/hourly_pm25.csv \
  --output-dir artifacts/eda_pm25 \
  --modeling-output data/processed/openaq_sensor_21632/modeling_pm25.csv \
  --gap-hours 24
```
