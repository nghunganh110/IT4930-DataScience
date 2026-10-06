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

## Làm việc nhóm: dùng chung dữ liệu

Repository **không lưu dữ liệu sinh ra, raw snapshot hay file `.env`**. Mỗi
thành viên phải dùng cùng một data snapshot do nhóm phát hành để EDA, feature,
model và báo cáo cho ra kết quả nhất quán.

### Lấy data snapshot

Người phụ trách dữ liệu (data owner) chia sẻ một thư mục Drive/OneDrive theo
phiên bản, ví dụ `openaq-21632-2017-01-01_2025-04-09`. Link snapshot hiện hành
được nhóm quản lý ngoài repository; hãy hỏi data owner trước khi bắt đầu.

Tải bốn file sau từ snapshot và đặt nguyên tên vào:

```text
data/processed/openaq_sensor_21632/
├── hourly_pm25.csv
├── manifest.json
├── quality_report.json
└── quality_report.md
```

- `hourly_pm25.csv` là canonical dataset bắt buộc.
- `manifest.json` ghi provenance, khoảng thời gian và checksum; hãy đối chiếu
  file này trước khi chạy thí nghiệm.
- Không tự sửa hoặc thay thế `hourly_pm25.csv`. Nếu phát hiện lỗi, báo data
  owner để phát hành snapshot version mới cho cả nhóm.
- Không upload `.env`, `OPENAQ_API_KEY`, raw API response hoặc data snapshot
  lên GitHub.

### Tái tạo modelling dataset và EDA tại máy cá nhân

Sau khi đặt snapshot đúng vị trí, chạy lệnh dưới đây. `modeling_pm25.csv` và
toàn bộ biểu đồ/báo cáo EDA là output tái tạo được nên không cần tải từ Drive
hay commit vào Git.

```bash
airguard-data eda-pm25 \
  --input data/processed/openaq_sensor_21632/hourly_pm25.csv \
  --output-dir artifacts/eda_pm25 \
  --modeling-output data/processed/openaq_sensor_21632/modeling_pm25.csv \
  --gap-hours 24
```

Nếu lệnh thành công, các thành viên có cùng `hourly_pm25.csv` sẽ nhận được
cùng modelling dataset, summary report và bốn biểu đồ EDA.
