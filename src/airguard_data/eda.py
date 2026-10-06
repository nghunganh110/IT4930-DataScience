import os
import json
from pathlib import Path
from typing import Dict, Any

import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def run_eda(input_csv: str, output_dir: str, modeling_output: str, gap_hours: int = 24) -> int:
    """Run EDA and prepare modeling dataset."""
    if gap_hours < 0:
        raise ValueError("gap_hours cannot be negative")

    os.makedirs(output_dir, exist_ok=True)
    Path(modeling_output).parent.mkdir(parents=True, exist_ok=True)

    # 1. Read and parse data
    df = pd.read_csv(input_csv)
    input_cols = list(df.columns)
    
    required_cols = {'timestamp_utc', 'pm2_5_ug_m3', 'is_valid'}
    missing = required_cols - set(input_cols)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(list(missing))}")

    input_rows = len(df)
    
    # Parse UTC timestamps
    df['timestamp_utc'] = pd.to_datetime(df['timestamp_utc'], utc=True)
    if 'timestamp_local' not in df.columns:
        df['timestamp_local'] = df['timestamp_utc'].dt.tz_convert('Asia/Ho_Chi_Minh').dt.tz_localize(None)
    else:
        df['timestamp_local'] = pd.to_datetime(df['timestamp_local'])

    # 2. Filter: valid, non-null, non-negative PM2.5
    valid_mask = (df['is_valid'] == True) & (df['pm2_5_ug_m3'].notnull()) & (df['pm2_5_ug_m3'] >= 0)
    df = df[valid_mask].copy()

    # Sort and deduplicate
    df = df.sort_values('timestamp_utc')
    df = df.drop_duplicates(subset=['timestamp_utc'], keep='first').copy()
    valid_rows = len(df)

    if valid_rows == 0:
        raise ValueError("No valid rows remaining after filtering.")

    # 3. Segments and Gap Details
    diff = df['timestamp_utc'].diff()
    diff_hours = diff.dt.total_seconds() / 3600.0
    missing_intervals = (diff_hours - 1.0).fillna(0)
    
    # missing_intervals > gap_hours breaks segment
    is_new_segment = missing_intervals > gap_hours
    df['segment_id'] = is_new_segment.cumsum() + 1
    
    missing_only = missing_intervals[missing_intervals > 0]
    total_missing_hours = float(missing_only.sum()) if not missing_only.empty else 0.0
    longest_gap = float(missing_only.max()) if not missing_only.empty else 0.0
    segment_breaking_gaps = int(is_new_segment.sum())

    # 4. Coverage
    min_time = df['timestamp_utc'].min()
    max_time = df['timestamp_utc'].max()
    expected_range = pd.date_range(start=min_time, end=max_time, freq='h', tz='UTC')
    expected_df = pd.DataFrame({'timestamp_utc': expected_range})
    expected_df['year_month'] = expected_df['timestamp_utc'].dt.strftime('%Y-%m')
    expected_df['year'] = expected_df['timestamp_utc'].dt.strftime('%Y')
    
    expected_monthly = expected_df.groupby('year_month').size()
    expected_yearly = expected_df.groupby('year').size()
    
    df['year_month_utc'] = df['timestamp_utc'].dt.strftime('%Y-%m')
    df['year_utc'] = df['timestamp_utc'].dt.strftime('%Y')
    
    observed_monthly = df.groupby('year_month_utc').size()
    observed_yearly = df.groupby('year_utc').size()
    
    coverage = {"monthly": [], "yearly": []}
    for ym in expected_monthly.index:
        exp = int(expected_monthly[ym])
        obs = int(observed_monthly.get(ym, 0))
        coverage["monthly"].append({
            "month": ym,
            "observed_hours": obs,
            "expected_hours": exp,
            "coverage_pct": round(obs / exp * 100.0, 2) if exp > 0 else 0.0
        })
        
    for yr in expected_yearly.index:
        exp = int(expected_yearly[yr])
        obs = int(observed_yearly.get(yr, 0))
        coverage["yearly"].append({
            "year": yr,
            "observed_hours": obs,
            "expected_hours": exp,
            "coverage_pct": round(obs / exp * 100.0, 2) if exp > 0 else 0.0
        })
        
    df = df.drop(columns=['year_month_utc', 'year_utc'])

    # 5. Features
    df['hour'] = df['timestamp_local'].dt.hour
    df['day_of_week'] = df['timestamp_local'].dt.dayofweek # 0=Mon, 6=Sun
    df['month'] = df['timestamp_local'].dt.month
    df['is_weekend'] = df['day_of_week'] >= 5

    # Retain canonical + new features in order
    new_features = ['segment_id', 'hour', 'day_of_week', 'month', 'is_weekend']
    out_cols = input_cols + new_features
    df_out = df[out_cols].copy()
    df_out.to_csv(modeling_output, index=False)

    # 6. Summary Statistics
    num_segments = df['segment_id'].nunique()
    pm_stats = df['pm2_5_ug_m3'].describe().to_dict()
    
    summary = {
        "input_rows": input_rows,
        "valid_rows": valid_rows,
        "time_range": [min_time.isoformat(), max_time.isoformat()],
        "pm25_statistics": {k: float(v) for k, v in pm_stats.items()},
        "largest_readings": df.nlargest(5, 'pm2_5_ug_m3')['pm2_5_ug_m3'].tolist(),
        "gap_hours_threshold": gap_hours,
        "num_segments": int(num_segments),
        "gap_details": {
            "total_missing_hours": total_missing_hours,
            "segment_breaking_gaps": segment_breaking_gaps,
            "longest_gap_hours": longest_gap
        },
        "coverage": coverage
    }
    
    with open(os.path.join(output_dir, 'eda_summary.json'), 'w') as f:
        json.dump(summary, f, indent=2)

    # 7. Charts
    _generate_charts(df, output_dir, coverage)
    
    # 8. Markdown Report
    _generate_report(summary, output_dir)

    return 0

def _generate_charts(df: pd.DataFrame, output_dir: str, coverage: dict):
    # 1. Monthly trend (average PM2.5 per month-year)
    plt.figure(figsize=(10, 5))
    df_plot = df.copy()
    df_plot['year_month'] = df_plot['timestamp_local'].dt.to_period('M').astype(str)
    monthly_avg = df_plot.groupby('year_month')['pm2_5_ug_m3'].mean()
    monthly_avg.plot(kind='bar')
    plt.title('Monthly Trend of PM2.5')
    plt.ylabel('Average PM2.5 (ug/m3)')
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'monthly_trend.png'))
    plt.close()

    # 2. Distribution
    plt.figure(figsize=(8, 5))
    df_plot['pm2_5_ug_m3'].plot(kind='hist', bins=50, logy=True)
    plt.title('Distribution of PM2.5')
    plt.xlabel('PM2.5 (ug/m3)')
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'distribution.png'))
    plt.close()

    # 3. Hour/Month Seasonality
    plt.figure(figsize=(10, 6))
    pivot = df_plot.pivot_table(values='pm2_5_ug_m3', index='hour', columns='month', aggfunc='mean')
    plt.imshow(pivot, aspect='auto', cmap='viridis', origin='lower')
    plt.colorbar(label='Mean PM2.5')
    plt.title('Hour/Month Seasonality')
    plt.xlabel('Month')
    plt.ylabel('Hour')
    plt.xticks(ticks=range(len(pivot.columns)), labels=pivot.columns)
    plt.yticks(ticks=range(len(pivot.index)), labels=pivot.index)
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'seasonality.png'))
    plt.close()

    # 4. Coverage Heatmap (year x month)
    plt.figure(figsize=(10, 5))
    cov_df = pd.DataFrame(coverage['monthly'])
    if not cov_df.empty:
        cov_df['year'] = cov_df['month'].str[:4]
        cov_df['mon'] = cov_df['month'].str[5:7]
        cov_pivot = cov_df.pivot(index='year', columns='mon', values='coverage_pct')
        plt.imshow(cov_pivot, aspect='auto', cmap='YlGn', origin='lower')
        plt.colorbar(label='Coverage %')
        plt.title('Coverage Heatmap (Year x Month)')
        plt.xlabel('Month')
        plt.ylabel('Year')
        plt.xticks(ticks=range(len(cov_pivot.columns)), labels=cov_pivot.columns)
        plt.yticks(ticks=range(len(cov_pivot.index)), labels=cov_pivot.index)
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'coverage.png'))
    plt.close()

def _generate_report(summary: Dict[str, Any], output_dir: str):
    report = [
        "# PM2.5 EDA Report",
        "",
        "## Overview",
        f"- **Input rows**: {summary['input_rows']}",
        f"- **Valid rows**: {summary['valid_rows']}",
        f"- **Time range**: {summary['time_range'][0]} to {summary['time_range'][1]}",
        "",
        "## Gap and Segmentation",
        f"- **Gap threshold**: {summary['gap_hours_threshold']} hours",
        f"- **Segments**: {summary['num_segments']}",
        f"- **Total missing hours**: {summary['gap_details']['total_missing_hours']}",
        f"- **Segment-breaking gaps**: {summary['gap_details']['segment_breaking_gaps']}",
        f"- **Longest gap (hours)**: {summary['gap_details']['longest_gap_hours']}",
        "",
        "## Summary Statistics (PM2.5)",
        "| Statistic | Value |",
        "|---|---|",
        f"| count | {summary['pm25_statistics']['count']} |",
        f"| mean | {summary['pm25_statistics']['mean']:.2f} |",
        f"| std | {summary['pm25_statistics']['std']:.2f} |",
        f"| min | {summary['pm25_statistics']['min']} |",
        f"| 25% | {summary['pm25_statistics']['25%']} |",
        f"| 50% | {summary['pm25_statistics']['50%']} |",
        f"| 75% | {summary['pm25_statistics']['75%']} |",
        f"| max | {summary['pm25_statistics']['max']} |",
        "",
        "**Largest Readings:**",
    ]
    for val in summary['largest_readings']:
        report.append(f"- {val}")
    
    report.append("")
    report.append("## Coverage")
    
    report.append("### Yearly Coverage")
    report.append("| Year | Observed Hours | Expected Hours | Coverage % |")
    report.append("|---|---|---|---|")
    for cov in summary['coverage']['yearly']:
        report.append(f"| {cov['year']} | {cov['observed_hours']} | {cov['expected_hours']} | {cov['coverage_pct']} |")
        
    report.append("")
    report.append("### Monthly Coverage")
    report.append("| Month | Observed Hours | Expected Hours | Coverage % |")
    report.append("|---|---|---|---|")
    for cov in summary['coverage']['monthly']:
        report.append(f"| {cov['month']} | {cov['observed_hours']} | {cov['expected_hours']} | {cov['coverage_pct']} |")
    
    with open(os.path.join(output_dir, 'eda-report.md'), 'w') as f:
        f.write("\n".join(report))
