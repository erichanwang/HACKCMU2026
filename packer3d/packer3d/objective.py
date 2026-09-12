"""Objective function and result metrics."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .geometry import EPS
from .models import Container, PackResult


@dataclass
class ObjectiveWeights:
    """What "better" means.  All terms are ~[0, 1]; lower objective is better.

    objective = unpacked * (priority-weighted volume left behind / total)
              + compact  * (bounding-box-from-origin volume / container volume)
              + com      * (axis-weighted CoM deviation, normalised by container dims)
              + height   * (stack height / H)                       (gravity only)
    """
    unpacked: float = 10.0
    compact: float = 1.0
    com: float = 6.0
    height: float = 0.5


def priority_volume_total(items) -> float:
    return sum(it.priority * it.volume for it in items)


def evaluate_state(state, items_by_id: dict, total_pv: float, weights: ObjectiveWeights) -> float:
    """Fast objective straight from a PackState (used inside the search loop)."""
    c = state.c
    unp = sum(items_by_id[u["id"]].priority * items_by_id[u["id"]].volume for u in state.unpacked)
    unp_frac = unp / total_pv if total_pv > 0 else 0.0
    ext = state.max_extent()
    compact = float(np.prod(ext) / np.prod(state.dims))
    val = weights.unpacked * unp_frac + weights.compact * compact + weights.com * state.com_deviation()
    if c.gravity:
        val += weights.height * float(ext[2] / state.dims[2])
    return float(val)


def compute_metrics(container: Container, placements, unpacked, items, weights: Optional[ObjectiveWeights] = None) -> dict:
    """Full metrics dict for a result (shared by naive, optimised, balanced and exhaustive)."""
    weights = weights or ObjectiveWeights()
    dims = np.asarray(container.dims, dtype=float)
    target = np.asarray(container.effective_com_target(), dtype=float)
    axis_w = np.asarray(container.effective_com_axis_weights(), dtype=float)
    items_by_id = {it.id: it for it in items}
    n = len(placements)
    if n:
        masses = np.array([p.mass for p in placements], dtype=float)
        centers = np.array([p.center for p in placements], dtype=float)
        bvols = np.array([p.dims[0] * p.dims[1] * p.dims[2] for p in placements], dtype=float)
        his = np.array([np.asarray(p.position) + np.asarray(p.dims) for p in placements], dtype=float)
        total_mass = float(masses.sum())
        if total_mass > EPS:
            com = (masses[:, None] * centers).sum(axis=0) / total_mass
            com_basis = "mass"
        else:
            com = (bvols[:, None] * centers).sum(axis=0) / bvols.sum()
            com_basis = "volume_centroid"
        extent = his.max(axis=0)
        true_vol = float(sum(p.volume for p in placements))
    else:
        total_mass, com, com_basis = 0.0, target.copy(), "empty"
        extent = np.zeros(3)
        true_vol = 0.0
    dv = (com - target) / dims
    com_dev = float(math.sqrt(float(np.sum(axis_w * dv * dv))))
    usable = container.usable_volume
    total_pv = priority_volume_total(items)
    unp_pv = sum(items_by_id[u["id"]].priority * items_by_id[u["id"]].volume for u in unpacked if u["id"] in items_by_id)
    unp_frac = unp_pv / total_pv if total_pv > 0 else 0.0
    compact = float(np.prod(extent) / np.prod(dims))
    height_frac = float(extent[2] / dims[2])
    objective = weights.unpacked * unp_frac + weights.compact * compact + weights.com * com_dev
    if container.gravity:
        objective += weights.height * height_frac
    return {
        "items_packed": n,
        "items_unpacked": len(unpacked),
        "volume_utilization": (true_vol / usable) if usable > 0 else 0.0,
        "bbox_extent_utilization": compact,
        "packed_volume": true_vol,
        "usable_volume": usable,
        "total_mass": total_mass,
        "max_mass": container.max_mass if math.isfinite(container.max_mass) else None,
        "com": [float(v) for v in com],
        "com_basis": com_basis,
        "com_target": [float(v) for v in target],
        "com_lateral_offset": float(math.hypot(com[0] - target[0], com[1] - target[1])),
        "com_offset_3d": float(np.linalg.norm(com - target)),
        "com_deviation": com_dev,
        "max_height": float(extent[2]),
        "max_height_fraction": height_frac,
        "unpacked_priority_volume_fraction": float(unp_frac),
        "objective": float(objective),
    }


def build_result(strategy: str, container: Container, placements, unpacked, items,
                 weights: Optional[ObjectiveWeights] = None, stats: Optional[dict] = None) -> PackResult:
    metrics = compute_metrics(container, placements, unpacked, items, weights)
    return PackResult(strategy, container, list(placements), [dict(u) for u in unpacked], metrics, stats or {})
