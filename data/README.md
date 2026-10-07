# Data directories

`data/raw/` is reserved for byte-for-byte snapshots downloaded by a live
feasibility probe or external data fetch. Every captured response is paired with a secret-free
metadata JSON containing its source URL (without query string), retrieval time,
requested bounds/parameters, SHA-256 hash, and HTTP validators when supplied.

Raw data and generated feasibility outputs are intentionally ignored by Git.
Do not add API keys, personal access tokens, or private exports to this
directory. The normalized, reproducible audit artifacts are written to
`artifacts/data_feasibility/` when the command is run.

`data/processed/` holds reproducible, generated canonical datasets. The
historical OpenAQ ingestion command writes one CSV, a manifest, and quality
reports per output directory. Similarly, weather ingestion writes `hourly_weather.csv`,
a manifest, and quality reports to its own directory. These processed directories
and files are also ignored by Git.

The `eda-pm25` command reads the canonical CSV and writes the segmentation
modeling dataset (e.g. `modeling_pm25.csv`) to `data/processed/` as well,
and places its visual reports in `artifacts/eda_pm25/`. The weather merge step
writes `pm25_weather.csv` alongside the processed dataset, with merge reports
in `artifacts/weather_integration/`. The `build-features` command generates
`features_pm25_weather.csv` inside `data/processed/` and logs its reports
in `artifacts/feature_engineering/`.

## Shared Data Snapshots

A shared weather snapshot belongs beside (but is versioned separately from) the OpenAQ snapshot.
When sharing datasets with the team, share the relevant `data/processed/<snapshot-name>/`
directory containing the canonical CSVs, manifest, and quality reports.
Never include `data/raw/` or any file containing secrets in a shared data snapshot.
