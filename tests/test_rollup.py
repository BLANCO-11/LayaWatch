"""Rollup aggregation tests (docs/observability-model.md section 6), hand-computed fixtures."""
from __future__ import annotations

import pytest

from layawatch.obs.rollup import floor_bucket, merge_rows, percentile, summarize

ZERO_ROW = {
    "count": 0,
    "sum": 0.0,
    "min": 0.0,
    "max": 0.0,
    "p50": 0.0,
    "p90": 0.0,
    "p95": 0.0,
    "p99": 0.0,
}


def test_percentile_nearest_rank_against_hand_computed_sample() -> None:
    # Nearest-rank definition: sort ascending, take index ceil(q * n) - 1 (no interpolation).
    # For this 1..10 sample (n = 10): p50 -> ceil(5.0) = rank 5 -> 5.0;
    # p90 -> ceil(9.0) = rank 9 -> 9.0; p95 -> ceil(9.5) = rank 10 -> 10.0;
    # p99 -> ceil(9.9) = rank 10 -> 10.0; q = 1.0 -> rank 10 -> 10.0.
    values = [float(v) for v in range(1, 11)]
    assert percentile(values, 0.5) == 5.0
    assert percentile(values, 0.9) == 9.0
    assert percentile(values, 0.95) == 10.0
    assert percentile(values, 0.99) == 10.0
    assert percentile(values, 1.0) == 10.0
    # Ranking happens after sorting, and the boundary sample is itself a member:
    # ceil(0.5 * 3) = rank 2 into [10, 20, 30] -> 20.
    assert percentile([30.0, 10.0, 20.0], 0.5) == 20.0


def test_percentile_empty_sample_and_q_bounds() -> None:
    assert percentile([], 0.5) == 0.0
    with pytest.raises(ValueError, match="q must be"):
        percentile([1.0, 2.0], 0.0)
    with pytest.raises(ValueError, match="q must be"):
        percentile([1.0, 2.0], 1.5)


def test_floor_bucket_edges_and_negative_epoch_math() -> None:
    # Step boundaries: the boundary timestamp itself starts the new bucket.
    assert floor_bucket(0, 10) == 0
    assert floor_bucket(10.0, 10) == 10
    assert floor_bucket(9.999, 10) == 0
    assert floor_bucket(60, 60) == 60
    assert floor_bucket(59.999, 60) == 0
    assert floor_bucket(3600.5, 3600) == 3600
    assert floor_bucket(3599, 3600) == 0
    # Negative epochs floor away from zero (// is floor division): -0.001 s belongs to -10.
    assert floor_bucket(-0.001, 10) == -10
    assert floor_bucket(-10, 10) == -10
    assert floor_bucket(-10.5, 10) == -20
    assert floor_bucket(-3600.5, 3600) == -7200


def test_floor_bucket_rejects_unpersisted_steps() -> None:
    with pytest.raises(ValueError, match="step must be"):
        floor_bucket(0, 15)
    with pytest.raises(ValueError, match="step must be"):
        floor_bucket(0, 1)


def test_summarize_field_by_field() -> None:
    values = [10.0, 20.0, 30.0, 40.0]
    # Percentiles hand-computed nearest-rank: p50 -> ceil(0.5*4)=2 -> 20.0;
    # p90 -> ceil(0.9*4)=ceil(3.6)=4 -> 40.0; p95/p99 also rank 4 -> 40.0.
    assert summarize(values) == {
        "count": 4,
        "sum": 100.0,
        "min": 10.0,
        "max": 40.0,
        "p50": 20.0,
        "p90": 40.0,
        "p95": 40.0,
        "p99": 40.0,
    }
    assert summarize([]) == ZERO_ROW


def test_merge_rows_exact_stats_and_weighted_percentiles() -> None:
    # Two child buckets merged into one parent bucket.
    bucket_a = summarize([10.0, 20.0, 30.0])
    bucket_b = summarize([100.0])
    assert bucket_a == {
        "count": 3,
        "sum": 60.0,
        "min": 10.0,
        "max": 30.0,
        "p50": 20.0,  # ceil(0.5*3)=2 -> sorted[1]
        "p90": 30.0,  # ceil(0.9*3)=3 -> sorted[2]
        "p95": 30.0,
        "p99": 30.0,
    }
    assert bucket_b == {**ZERO_ROW, "count": 1, "sum": 100.0, "min": 100.0, "max": 100.0,
                        "p50": 100.0, "p90": 100.0, "p95": 100.0, "p99": 100.0}
    merged = merge_rows([bucket_a, bucket_b])
    assert merged == {
        # count/sum/min/max merge exactly.
        "count": 4,
        "sum": 160.0,
        "min": 10.0,
        "max": 100.0,
        # Percentiles: child-count-weighted average, e.g. (20*3 + 100*1) / 4 = 40.
        "p50": 40.0,
        "p90": 47.5,  # (30*3 + 100*1) / 4
        "p95": 47.5,
        "p99": 47.5,
    }


def test_merge_rows_single_row_is_identity_and_empty_is_zero() -> None:
    row = summarize([10.0, 20.0, 30.0, 40.0])
    assert merge_rows([row]) == row
    assert merge_rows([]) == ZERO_ROW
    assert merge_rows([]) == summarize([])
