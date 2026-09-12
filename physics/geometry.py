"""Shared OBB geometry: quaternion -> world axes, half-extents, vertices.

Every submodule (collision, containment, support) must build OBBs through
`obb_from` rather than re-deriving rotation math, so the whole physics layer
agrees on one quaternion convention.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from physics.schema import Container, Object


@dataclass
class OBB:
    center: np.ndarray  # (3,)
    axes: np.ndarray  # (3, 3), columns are world-space unit local x/y/z axes
    half_extents: np.ndarray  # (3,), along axes columns respectively
    id: str


def quat_to_matrix(q: tuple[float, float, float, float]) -> np.ndarray:
    """(x, y, z, w) -> 3x3 rotation matrix. Raises ValueError if not unit-length."""
    x, y, z, w = q
    n = x * x + y * y + z * z + w * w
    if not np.isfinite(n) or n < 1e-12:
        raise ValueError(f"invalid quaternion {q!r}: zero or non-finite norm")
    if abs(n - 1.0) > 1e-3:
        # normalize defensively rather than silently producing a skewed matrix
        s = n**-0.5
        x, y, z, w = x * s, y * s, z * s, w * s
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def obb_from(entity: Object | Container) -> OBB:
    dims = entity.dimensions
    if len(dims) != 3 or not all(np.isfinite(d) and d > 0 for d in dims):
        raise ValueError(f"{entity.id}: invalid dimensions {dims!r}")
    pos = np.asarray(entity.position, dtype=float)
    if pos.shape != (3,) or not np.all(np.isfinite(pos)):
        raise ValueError(f"{entity.id}: invalid position {entity.position!r}")
    rot = quat_to_matrix(entity.rotation)
    half_extents = np.asarray(dims, dtype=float) / 2.0
    return OBB(center=pos, axes=rot, half_extents=half_extents, id=entity.id)


# Corner sign pattern, fixed order: (-,-,-), (-,-,+), (-,+,-), ... (+,+,+).
SIGNS = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)], dtype=float)


def obb_vertices(obb: OBB) -> np.ndarray:
    """8x3 array of world-space corner points, in `SIGNS` order."""
    offsets = SIGNS * obb.half_extents  # (8, 3) in local axis units
    return obb.center + offsets @ obb.axes.T
