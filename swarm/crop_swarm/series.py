"""Load the WFP Kenya panel and turn it into dense monthly price arrays, one per series.

Index convention used everywhere in crop_swarm:
    month index = year * 12 + (month - 1)            (same as features.per_i)
    dense index  = month index - grid_start           (grid_start is a multiple of 12)
So dense index i is calendar month (i % 12) with January = 0.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import numpy as np

SWARM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONOREPO_DIR = os.path.dirname(SWARM_DIR)
PIPELINE_DIR = os.path.join(MONOREPO_DIR, "pipeline")
DEFAULT_SNAPSHOT = os.path.join(MONOREPO_DIR, "data", "wfp_snapshot.csv.gz")

if PIPELINE_DIR not in sys.path:  # so `features` / `retrain` import from the monorepo pipeline
    sys.path.insert(0, PIPELINE_DIR)

import features as pipeline_features  # noqa: E402  (monorepo/pipeline/features.py, read only)

KEY = list(pipeline_features.KEY)  # ["commodity", "market", "unit", "pricetype"]


@dataclass
class DenseSet:
    grid_start: int            # month index of dense index 0 (multiple of 12)
    n: int                     # length of every array
    series: dict               # key tuple -> float array (NaN = not observed)


def load_panel(path: str = DEFAULT_SNAPSHOT):
    """Monthly panel exactly as the pipeline builds it (median per series-month, KES, tracked crops)."""
    raw = pipeline_features.read_wfp_csv(path)
    return pipeline_features.monthly_panel(raw)


def to_dense(panel) -> DenseSet:
    idx = (panel["per"].dt.year * 12 + panel["per"].dt.month - 1).astype(int)
    grid_start = int(idx.min()) // 12 * 12
    n = int(idx.max()) - grid_start + 1
    frame = panel.assign(_i=idx.to_numpy() - grid_start)
    series = {}
    for key, grp in frame.groupby(KEY, sort=True):
        arr = np.full(n, np.nan)
        arr[grp["_i"].to_numpy()] = grp["price"].to_numpy(dtype=float)
        series[tuple(key)] = arr
    return DenseSet(grid_start=grid_start, n=n, series=series)
