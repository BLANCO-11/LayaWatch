"""Pure rollup aggregation for metric buckets (docs/observability-model.md section 6).

All percentile values are bucket-level: ``merge_rows`` averages child-bucket percentiles
weighted by child count, which is the documented approximation the Metrics view states in
its section meta. Empty samples produce an all-zero summary; callers write no row for a
count of 0, so the zeros never reach the database.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

_STEPS = (10, 60, 3600)
_PERCENTILES = (("p50", 0.5), ("p90", 0.9), ("p95", 0.95), ("p99", 0.99))
_ZERO = {
    "count": 0,
    "sum": 0.0,
    "min": 0.0,
    "max": 0.0,
    "p50": 0.0,
    "p90": 0.0,
    "p95": 0.0,
    "p99": 0.0,
}


def floor_bucket(ts: float, step: int) -> int:
    """Epoch-aligned bucket start for ``ts``; floor semantics, negative epochs included.

    ``step`` must be one of the persisted rollup steps 10, 60 or 3600 seconds.
    """
    if step not in _STEPS:
        raise ValueError(f"step must be one of {_STEPS}, got {step!r}")
    return int(ts // step) * step


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile of ``values`` for ``q`` in (0, 1]; empty sample -> 0.0.

    Nearest-rank: sort ascending, take index ``ceil(q * n) - 1``. No interpolation, so the
    result is always an element of the sample.
    """
    if not 0.0 < q <= 1.0:
        raise ValueError(f"q must be in (0, 1], got {q!r}")
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = math.ceil(q * len(ordered))
    return float(ordered[rank - 1])


def summarize(values: Sequence[float]) -> dict:
    """count/sum/min/max plus p50/p90/p95/p99 for one bucket; empty sample -> all zeros."""
    if not values:
        return dict(_ZERO)
    summary: dict = {
        "count": len(values),
        "sum": float(sum(values)),
        "min": float(min(values)),
        "max": float(max(values)),
    }
    for name, q in _PERCENTILES:
        summary[name] = percentile(values, q)
    return summary


def merge_rows(rows: Sequence[dict]) -> dict:
    """Merge ``summarize()``-shaped child rows into one parent-bucket summary.

    count, sum, min and max merge exactly. Percentiles are the child-count-weighted average
    of the child percentiles (``sum(child p * child count) / sum(child count)``) - the
    documented bucket-level approximation, not an exact global percentile. Returns only the
    eight stat keys; callers re-attach bucket/step/metric dimensions. Empty input yields the
    zero row.
    """
    count = 0
    total = 0.0
    minimums: list[float] = []
    maximums: list[float] = []
    weighted = {name: 0.0 for name, _ in _PERCENTILES}
    for row in rows:
        child = int(row["count"])
        count += child
        total += row["sum"]
        if child:
            minimums.append(row["min"])
            maximums.append(row["max"])
            for name, _ in _PERCENTILES:
                weighted[name] += row[name] * child
    merged: dict = {
        "count": count,
        "sum": total,
        "min": float(min(minimums)) if minimums else 0.0,
        "max": float(max(maximums)) if maximums else 0.0,
    }
    for name, _ in _PERCENTILES:
        merged[name] = weighted[name] / count if count else 0.0
    return merged
