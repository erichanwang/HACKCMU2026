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


# ---------------------------------------------------------------------------
# Footprints / convex prisms (LiDAR hull support)
# ---------------------------------------------------------------------------
def convex_hull_2d(points) -> np.ndarray:
    """Convex hull of 2D points (Andrew's monotone chain), CCW, no duplicates,
    no collinear intermediate vertices. Returns an (m, 2) float array; m may be
    1 or 2 for degenerate input."""
    pts = np.unique(np.asarray(points, dtype=float).reshape(-1, 2), axis=0)  # sorted lexicographically
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list = []
    for p in pts[::-1]:
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return np.array(lower[:-1] + upper[:-1])


def polygon_area_2d(poly: np.ndarray) -> float:
    """Shoelace area of a CCW polygon (positive)."""
    if len(poly) < 3:
        return 0.0
    x, z = poly[:, 0], poly[:, 1]
    return float(0.5 * abs(np.dot(x, np.roll(z, -1)) - np.dot(z, np.roll(x, -1))))


def convex_clip_2d(subject: np.ndarray, clipper: np.ndarray) -> np.ndarray:
    """Sutherland-Hodgman intersection of two CCW convex polygons -> CCW polygon
    (k, 2), possibly empty (0, 2)."""
    out = [tuple(p) for p in np.asarray(subject, dtype=float)]
    clip = np.asarray(clipper, dtype=float)
    m = len(clip)
    for i in range(m):
        if not out:
            break
        a, b = clip[i], clip[(i + 1) % m]
        ex, ez = b[0] - a[0], b[1] - a[1]

        def inside(p):
            return ex * (p[1] - a[1]) - ez * (p[0] - a[0]) >= -1e-15

        def isect(p, q):
            dx, dz = q[0] - p[0], q[1] - p[1]
            den = ex * dz - ez * dx
            if abs(den) < 1e-18:
                return q
            t = (ex * (a[1] - p[1]) - ez * (a[0] - p[0])) / den
            return (p[0] + t * dx, p[1] + t * dz)

        nxt = []
        prev = out[-1]
        for cur in out:
            if inside(cur):
                if not inside(prev):
                    nxt.append(isect(prev, cur))
                nxt.append(cur)
            elif inside(prev):
                nxt.append(isect(prev, cur))
            prev = cur
        out = nxt
    return np.array(out, dtype=float).reshape(-1, 2)


def polygon_centroid_2d(poly: np.ndarray) -> np.ndarray:
    """Area centroid of a CCW convex polygon (m, 2) -> (2,). Falls back to the
    vertex mean for degenerate (zero-area) input."""
    if len(poly) < 3:
        return poly.mean(axis=0)
    x, z = poly[:, 0], poly[:, 1]
    xn, zn = np.roll(x, -1), np.roll(z, -1)
    cr = x * zn - xn * z
    a = cr.sum() / 2.0
    if abs(a) <= 1e-18:
        return poly.mean(axis=0)
    return np.array([((x + xn) * cr).sum(), ((z + zn) * cr).sum()]) / (6.0 * a)


def footprint_local(entity: Object | Container) -> np.ndarray:
    """The entity's footprint in its LOCAL (x, z) plane as a CCW convex polygon
    (m, 2). Boxes (no `footprint`) return their 4 rectangle corners CCW:
    (-hx,-hz), (hx,-hz), (hx,hz), (-hx,hz). A supplied footprint is hulled and
    validated: >= 3 points with positive area, finite, and inside the
    dimensions' bounding rectangle (1e-6 slack) -- the box must remain a
    conservative envelope of the prism."""
    hx, hz = entity.dimensions[0] / 2.0, entity.dimensions[2] / 2.0
    fp = getattr(entity, "footprint", None)
    if fp is None:
        return np.array([[-hx, -hz], [hx, -hz], [hx, hz], [-hx, hz]], dtype=float)
    arr = np.asarray(fp, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 2 or not np.all(np.isfinite(arr)):
        raise ValueError(f"{entity.id}: invalid footprint {fp!r}")
    hull = convex_hull_2d(arr)
    if len(hull) < 3 or polygon_area_2d(hull) <= 1e-12:
        raise ValueError(f"{entity.id}: footprint is degenerate (needs >= 3 non-collinear points)")
    if np.any(np.abs(hull[:, 0]) > hx + 1e-6) or np.any(np.abs(hull[:, 1]) > hz + 1e-6):
        raise ValueError(f"{entity.id}: footprint exceeds dimensions {entity.dimensions!r} in local x/z")
    return hull


def prism_vertices(obb: OBB, footprint: np.ndarray) -> np.ndarray:
    """World-space vertices of the convex prism: bottom ring (local y = -hy) in
    footprint order, then the top ring (+hy) in the same order -> (2m, 3)."""
    m = len(footprint)
    hy = obb.half_extents[1]
    local = np.empty((2 * m, 3))
    local[:m, 0] = footprint[:, 0]
    local[:m, 2] = footprint[:, 1]
    local[:m, 1] = -hy
    local[m:, 0] = footprint[:, 0]
    local[m:, 2] = footprint[:, 1]
    local[m:, 1] = hy
    return obb.center + local @ obb.axes.T
