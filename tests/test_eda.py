import json
import pytest
import pandas as pd
from pathlib import Path

from airguard_data.eda import run_eda

@pytest.fixture
def sample_csv(tmp_path):
    df = pd.DataFrame({
        'timestamp_utc': [
            '2024-01-01T00:00:00Z', # valid
            '2024-01-01T01:00:00Z', # valid
            '2024-01-01T02:00:00Z', # invalid pm2.5
            '2024-01-01T03:00:00Z', # not valid flag
            '2024-01-02T04:00:00Z', # gap of 25 hours (24 missing hours) - threshold exactly
            '2024-01-03T06:00:00Z', # gap of 26 hours (25 missing hours) - over threshold
        ],
        'timestamp_local': [
            '2024-01-01T07:00:00',
            '2024-01-01T08:00:00',
            '2024-01-01T09:00:00',
            '2024-01-01T10:00:00',
            '2024-01-02T11:00:00',
            '2024-01-03T13:00:00',
        ],
        'pm2_5_ug_m3': [10.0, 15.0, -5.0, 20.0, 25.0, 30.0],
        'is_valid': [True, True, True, False, True, True],
        'location_id': [1, 1, 1, 1, 1, 1]
    })
    path = tmp_path / "input.csv"
    df.to_csv(path, index=False)
    return str(path)

@pytest.fixture
def empty_csv(tmp_path):
    df = pd.DataFrame({
        'timestamp_utc': [],
        'timestamp_local': [],
        'pm2_5_ug_m3': [],
        'is_valid': []
    })
    path = tmp_path / "empty.csv"
    df.to_csv(path, index=False)
    return str(path)

def test_validity_filtering(sample_csv, tmp_path):
    out_dir = tmp_path / "out"
    mod_out = tmp_path / "mod.csv"
    
    run_eda(sample_csv, str(out_dir), str(mod_out), gap_hours=24)
    
    df_mod = pd.read_csv(mod_out)
    assert len(df_mod) == 4
    assert df_mod['pm2_5_ug_m3'].tolist() == [10.0, 15.0, 25.0, 30.0]



def test_exact_gap_thresholds(tmp_path):
    # If gap_hours = 24.
    # diff = 25 hours -> missing = 24 hours -> missing <= 24 -> same segment.
    # diff = 26 hours -> missing = 25 hours -> missing > 24 -> NEW segment.
    df = pd.DataFrame({
        'timestamp_utc': [
            '2024-01-01T00:00:00Z', 
            '2024-01-02T01:00:00Z', # 25h diff -> 24h missing. Exactly threshold, same segment.
            '2024-01-03T03:00:00Z', # 26h diff -> 25h missing. Over threshold, new segment.
        ],
        'timestamp_local': [
            '2024-01-01T07:00:00',
            '2024-01-02T08:00:00',
            '2024-01-03T10:00:00',
        ],
        'pm2_5_ug_m3': [10.0, 20.0, 30.0],
        'is_valid': [True, True, True]
    })
    in_csv = tmp_path / "exact.csv"
    df.to_csv(in_csv, index=False)
    
    out_dir = tmp_path / "out"
    mod_out = tmp_path / "mod.csv"
    
    run_eda(str(in_csv), str(out_dir), str(mod_out), gap_hours=24)
    
    df_mod = pd.read_csv(mod_out)
    segs = df_mod['segment_id'].tolist()
    assert segs == [1, 1, 2]

def test_deterministic_features(tmp_path):
    df = pd.DataFrame({
        'timestamp_utc': ['2024-01-01T00:00:00Z'], # Monday
        'timestamp_local': ['2024-01-01T07:00:00'], # Monday, hour 7
        'pm2_5_ug_m3': [10.0],
        'is_valid': [True]
    })
    in_csv = tmp_path / "feat.csv"
    df.to_csv(in_csv, index=False)
    
    out_dir = tmp_path / "out"
    mod_out = tmp_path / "mod.csv"
    
    run_eda(str(in_csv), str(out_dir), str(mod_out), gap_hours=24)
    df_mod = pd.read_csv(mod_out)
    
    assert df_mod['hour'].iloc[0] == 7
    assert df_mod['day_of_week'].iloc[0] == 0 # Monday
    assert df_mod['month'].iloc[0] == 1
    assert not df_mod['is_weekend'].iloc[0]

def test_artifact_e2e(tmp_path):
    df = pd.DataFrame({
        'timestamp_utc': ['2024-01-01T00:00:00Z', '2024-01-01T01:00:00Z'],
        'timestamp_local': ['2024-01-01T07:00:00', '2024-01-01T08:00:00'],
        'pm2_5_ug_m3': [10.0, 20.0],
        'is_valid': [True, True]
    })
    in_csv = tmp_path / "e2e.csv"
    df.to_csv(in_csv, index=False)
    
    out_dir = tmp_path / "out"
    mod_out = tmp_path / "mod.csv"
    
    run_eda(str(in_csv), str(out_dir), str(mod_out), gap_hours=24)
    
    assert (out_dir / "eda_summary.json").exists()
    assert (out_dir / "eda-report.md").exists()
    assert (out_dir / "monthly_trend.png").exists()
    assert (out_dir / "distribution.png").exists()
    assert (out_dir / "seasonality.png").exists()
    assert (out_dir / "coverage.png").exists()
    assert Path(mod_out).exists()

def test_invalid_input(empty_csv, tmp_path):
    out_dir = tmp_path / "out"
    mod_out = tmp_path / "mod.csv"
    
    with pytest.raises(ValueError, match="No valid rows remaining"):
        run_eda(empty_csv, str(out_dir), str(mod_out), gap_hours=24)

def test_negative_gap(sample_csv, tmp_path):
    out_dir = tmp_path / "out"
    mod_out = tmp_path / "mod.csv"
    
    with pytest.raises(ValueError, match="gap_hours cannot be negative"):
        run_eda(sample_csv, str(out_dir), str(mod_out), gap_hours=-1)

def test_missing_required_columns(tmp_path):
    df = pd.DataFrame({
        'timestamp_utc': ['2024-01-01T00:00:00Z'],
        'pm2_5_ug_m3': [10.0]
        # missing is_valid
    })
    in_csv = tmp_path / "missing.csv"
    df.to_csv(in_csv, index=False)
    
    out_dir = tmp_path / "out"
    mod_out = tmp_path / "mod.csv"
    
    with pytest.raises(ValueError, match="Missing required columns"):
        run_eda(str(in_csv), str(out_dir), str(mod_out), gap_hours=24)

def test_summary_gap_metrics(tmp_path):
    df = pd.DataFrame({
        'timestamp_utc': [
            '2024-01-01T00:00:00Z', 
            '2024-01-01T02:00:00Z', # 1 missing hour
            '2024-01-03T05:00:00Z', # 51 missing hours (2 days 3 hrs = 51 hours)
        ],
        'pm2_5_ug_m3': [10.0, 20.0, 30.0],
        'is_valid': [True, True, True]
    })
    in_csv = tmp_path / "gaps.csv"
    df.to_csv(in_csv, index=False)
    
    out_dir = tmp_path / "out"
    mod_out = tmp_path / "mod.csv"
    
    run_eda(str(in_csv), str(out_dir), str(mod_out), gap_hours=24)
    
    summary_file = out_dir / "eda_summary.json"
    with open(summary_file) as f:
        summary = json.load(f)
        
    gap_details = summary['gap_details']
    assert gap_details['total_missing_hours'] == 51.0
    assert gap_details['segment_breaking_gaps'] == 1
    assert gap_details['longest_gap_hours'] == 50.0

def test_monthly_yearly_coverage(tmp_path):
    df = pd.DataFrame({
        'timestamp_utc': [
            '2024-12-31T23:00:00Z', 
            '2025-01-01T00:00:00Z',
            '2025-01-01T01:00:00Z',
        ],
        'pm2_5_ug_m3': [10.0, 20.0, 30.0],
        'is_valid': [True, True, True]
    })
    in_csv = tmp_path / "cov.csv"
    df.to_csv(in_csv, index=False)
    
    out_dir = tmp_path / "out"
    mod_out = tmp_path / "mod.csv"
    
    run_eda(str(in_csv), str(out_dir), str(mod_out), gap_hours=24)
    
    summary_file = out_dir / "eda_summary.json"
    with open(summary_file) as f:
        summary = json.load(f)
        
    coverage = summary['coverage']
    
    # Assert monthly
    monthly = coverage['monthly']
    dec_cov = next(c for c in monthly if c['month'] == '2024-12')
    jan_cov = next(c for c in monthly if c['month'] == '2025-01')
    
    assert dec_cov['observed_hours'] == 1
    # expect 1 hour (from 2024-12-31T23 to 2024-12-31T23)
    assert dec_cov['expected_hours'] == 1
    
    assert jan_cov['observed_hours'] == 2
    # expected for Jan: 2025-01-01T00 to 2025-01-01T01 = 2 hours
    assert jan_cov['expected_hours'] == 2
    
    # Assert yearly
    yearly = coverage['yearly']
    yr_2024 = next(c for c in yearly if c['year'] == '2024')
    yr_2025 = next(c for c in yearly if c['year'] == '2025')
    
    assert yr_2024['observed_hours'] == 1
    assert yr_2024['expected_hours'] == 1
    
    assert yr_2025['observed_hours'] == 2
    assert yr_2025['expected_hours'] == 2
