"""OBB-vs-OBB collision test via the Separating Axis Theorem (SAT).

Assumptions
-----------
- Both boxes are convex, oriented bounding boxes (`OBB` from `physics.geometry`):
  a center, 3 orthonormal world-space axes (columns of `axes`), and 3 half-extents.
  This module tests boxes only, not the underlying meshes -- an OBB is a
  conservative/approximate proxy for whatever object it represents.
- Coordinate system matches `physics.schema`: meters, X=right/Y=up/Z=forward,
  right-handed. SAT itself is coordinate-free; this just fixes units for
  `penetration_depth_m`.
- Two convex polyhedra are separated iff there exists a separating axis among:
  the 3 face normals of A, the 3 face normals of B, and the 9 axes formed by
  cross products of A's edges with B's edges (Gottschalk et al.). For boxes,
  face normals are just the box's own local axes. 15 axes total, checked in a
  fixed order (A's axes, B's axes, 9 cross products) -- ties in the "smallest
  overlap" search keep whichever axis was checked first.

Epsilon semantics
------------------
`epsilon` is a distance-space slack applied to the *overlap along the current
axis*, not to the SAT separation test that decides collision. Concretely: for
each axis, the raw overlap is `radius_sum - center_dist`. If, for ANY axis,
`overlap <= epsilon`, the boxes are NOT colliding (the boxes are separated,
touching, or overlapping only by a sliver smaller than epsilon) -- that axis
is still a valid separating axis for reporting purposes. Two boxes touching
exactly (overlap == 0) therefore report `colliding=False` with
`penetration_depth_m=0.0` for any epsilon >= 0. An overlap of
`epsilon < overlap` on every one of the 15 axes reports `colliding=True`, with
`penetration_depth_m` = the minimum such overlap across all axes (the MTV
magnitude) and `axis` its (unit) direction. This also means a razor-thin true
overlap (e.g. 1e-4 m) with a small epsilon (e.g. 1e-6) still reports as
colliding, while numerically-touching boxes (overlap ~ 1e-9 from float error)
correctly report as not colliding, at the default epsilon=1e-6.

Complexity
----------
`check_collision`: O(1) -- 15 fixed axes, O(1) work each (project 8 implicit
half-extents via `|axis . column|` dot products, not the 8 vertices).
`aabb_overlap`: O(1) -- 8 vertices per box via `obb_vertices`, one bbox each.
Running either helper, or `check_collision`, over a list of n objects (all
pairs) is O(n^2) -- there is no spatial index here; callers needing more must
add their own broad-phase (grid/BVH) in front of this module.

Known failure modes
--------------------
- Boxes only: concave or non-box shapes are not modeled; an OBB may report a
  collision (or lack of one) that the true mesh would not.
- Near-parallel edges: a cross-product axis for two nearly-parallel edges has
  a tiny (near-zero) magnitude and is numerically unreliable as a direction,
  so it is skipped (see `_CROSS_AXIS_MIN_NORM`) rather than normalized and
  risking a divide-by-tiny-number blowup. Skipping an axis can only make
  `check_collision` under-detect a separation that axis would have found; in
  the box-box case the other 14 axes are exhaustive for all *other*
  configurations, but two boxes separated *only* by that one degenerate axis
  (edges genuinely parallel) can, in rare cases, be misreported as colliding.
  This is the classic SAT edge-case and is inherent to the algorithm.
- Large coordinate magnitudes: float64 has ~15-17 significant decimal digits;
  at coordinates on the order of 1e6 m the sub-millimeter precision this
  module aims for degrades. Not addressed here -- keep scene coordinates near
  the origin (true for a suitcase-packing scene, order 1 m).
- `contact_point` is a documented approximation (see its docstring below), not
  a physically exact contact manifold.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from physics.geometry import OBB, obb_vertices

_CROSS_AXIS_MIN_NORM = 1e-8  # below this, treat A-edge x B-edge as degenerate/parallel


@dataclass
class CollisionResult:
    colliding: bool
    penetration_depth_m: float = 0.0
    axis: np.ndarray = None  # unit vector, MTV direction (A -> B convention), or None
    contact_point: np.ndarray = None  # approximate, see check_collision docstring
    a_id: str | None = None  # OBB.id of `a`, for callers that don't thread ids themselves
    b_id: str | None = None  # OBB.id of `b`


def aabb_overlap(a: OBB, b: OBB) -> bool:
    """Cheap world-axis-aligned bounding-box overlap test, for broad-phase pruning.

    Computes each OBB's world AABB from its 8 vertices (O(1): 8 points per box)
    and checks the 3 interval overlaps. This is a *necessary* condition for
    OBB-OBB collision, not sufficient -- always false negatives are impossible,
    but it can return True for non-colliding OBBs (their tight AABBs overlap
    even though the rotated boxes don't). Use it to skip full SAT for pairs
    that are obviously far apart; `check_collision` already does this
    internally with a small epsilon-aware margin.
    """
    av = obb_vertices(a)
    bv = obb_vertices(b)
    a_min, a_max = av.min(axis=0), av.max(axis=0)
    b_min, b_max = bv.min(axis=0), bv.max(axis=0)
    return bool(np.all(a_max >= b_min) and np.all(b_max >= a_min))


def check_collision(a: OBB, b: OBB, epsilon: float = 1e-6) -> CollisionResult:
    """SAT test for two OBBs. See module docstring for epsilon semantics.

    `axis` is the unit-length minimum-translation-vector (MTV) direction: the
    axis (among the 15 candidates) with the smallest positive overlap, i.e.
    the axis SAT would push along to separate the boxes with least motion.
    It is oriented to point from A's center towards B's center.

    `contact_point` is an approximation: the midpoint, along the MTV axis, of
    the overlapping interval of the two boxes' projections onto that axis,
    offset from A's center. It is a reasonable single point inside the
    overlap region but is NOT a true contact manifold/patch -- for face-face
    contact the real contact region is a polygon, not a point.
    """
    d = b.center - a.center

    # Cheap broad-phase: if AABBs (with epsilon slack) don't overlap, bail out.
    av = obb_vertices(a)
    bv = obb_vertices(b)
    if np.any(av.max(axis=0) + epsilon < bv.min(axis=0)) or np.any(
        bv.max(axis=0) + epsilon < av.min(axis=0)
    ):
        return CollisionResult(colliding=False, penetration_depth_m=0.0, a_id=a.id, b_id=b.id)

    candidate_axes = []
    for i in range(3):
        candidate_axes.append(a.axes[:, i])
    for j in range(3):
        candidate_axes.append(b.axes[:, j])
    for i in range(3):
        for j in range(3):
            cross = np.cross(a.axes[:, i], b.axes[:, j])
            n = np.linalg.norm(cross)
            if n < _CROSS_AXIS_MIN_NORM:
                continue  # near-parallel edges: degenerate axis, skip rather than /tiny
            candidate_axes.append(cross / n)

    min_overlap = np.inf
    min_axis = None
    for axis in candidate_axes:
        # Projected half-extent of each box onto `axis` (sum of |axis . column_k| * half_extent_k).
        ra = float(np.sum(np.abs(axis @ a.axes) * a.half_extents))
        rb = float(np.sum(np.abs(axis @ b.axes) * b.half_extents))
        center_dist = abs(float(axis @ d))
        overlap = ra + rb - center_dist
        if overlap <= epsilon:
            # A valid separating axis (touching-or-less counts as separated).
            return CollisionResult(colliding=False, penetration_depth_m=0.0, a_id=a.id, b_id=b.id)
        if overlap < min_overlap:
            min_overlap = overlap
            min_axis = axis

    # No separating axis found among all 15 (minus skipped degenerate ones) -> colliding.
    axis = min_axis
    if float(axis @ d) < 0:
        axis = -axis  # orient A -> B

    # Approximate contact point: project both boxes' extents onto the MTV axis
    # and take the midpoint of the overlapping interval, expressed as a world point.
    ra = float(np.sum(np.abs(axis @ a.axes) * a.half_extents))
    rb = float(np.sum(np.abs(axis @ b.axes) * b.half_extents))
    a_c = float(axis @ a.center)
    b_c = float(axis @ b.center)
    lo = max(a_c - ra, b_c - rb)
    hi = min(a_c + ra, b_c + rb)
    mid = (lo + hi) / 2.0
    # Reconstruct a world point: start from A's center, move to `mid` along axis,
    # keep the perpendicular component from the midpoint between the two centers.
    perp_ref = (a.center + b.center) / 2.0
    contact_point = perp_ref + (mid - float(axis @ perp_ref)) * axis

    return CollisionResult(
        colliding=True,
        penetration_depth_m=float(min_overlap),
        axis=axis,
        contact_point=contact_point,
        a_id=a.id,
        b_id=b.id,
    )
