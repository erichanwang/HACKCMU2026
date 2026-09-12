"""Numeric helpers shared by every module.

All geometric comparisons in packer3d go through ``EPS``; positions are rounded to
``ROUND`` decimals so repeated flush placements do not drift.
"""
from __future__ import annotations

import math

EPS = 1e-6      # tolerance for every geometric comparison
ROUND = 9       # positions are rounded to 1e-9


def rnd(x: float) -> float:
    return float(round(float(x), ROUND))


def rnd3(v) -> tuple[float, float, float]:
    return (rnd(v[0]), rnd(v[1]), rnd(v[2]))


def is_finite_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
