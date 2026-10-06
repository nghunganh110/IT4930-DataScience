# Data directories

`data/raw/` is reserved for byte-for-byte snapshots downloaded by a live
feasibility probe. Every captured response is paired with a secret-free
metadata JSON containing its source URL (without query string), retrieval time,
SHA-256 hash, and HTTP validators when supplied.

Raw data and generated feasibility outputs are intentionally ignored by Git.
Do not add API keys, personal access tokens, or private exports to this
directory. The normalized, reproducible audit artifacts are written to
`artifacts/data_feasibility/` when the command is run.

`data/processed/` holds reproducible, generated canonical datasets. The
historical OpenAQ ingestion command writes one CSV, a manifest, and quality
reports per output directory; those files are also ignored by Git.
