"""Static stability heuristic for packed objects.

This is a STATIC STABILITY HEURISTIC, NOT a full rigid-body simulator. It does
not integrate forces/torques over time, does not model friction, and does not
predict dynamic tipping under acceleration (braking, turning, being dropped).
It answers one narrower question: "given these final resting poses, does each
object look adequately supported and balanced under gravity alone?"

Assumptions:
- Rigid bodies, uniform density per object, so an object's center of mass is
  simply its OBB center (`physics.geometry.obb_from(obj).center`).
- Gravity acts along -Y (Y is up, per physics.schema).
- Static equilibrium approximation: we check whether the center-of-mass XZ
  projection falls within the supported footprint, not whether forces/torques
  actually balance (no friction, no normal-force distribution solved).
- Footprint approximation (KNOWN LIMITATION): an object's bottom (or top)
  contact footprint is approximated as the axis-aligned (in world X/Z)
  bounding rectangle of the 4 lowest-Y (or highest-Y) OBB vertices projected
  onto the XZ plane. This is exact only when the object has no roll/pitch
  (rotation purely about the world Y axis, or no rotation at all) — for a
  tilted box the true contact footprint is a rotated quadrilateral, but we
  use its XZ bounding rectangle instead of a true convex hull / rotated
  polygon. This over-estimates footprint area for tilted objects. The scene
  validity assumed upstream (non-colliding, contained) means most packed
  objects are axis-aligned or Y-only-rotated in practice, so this is an
  acceptable approximation for hackathon scope.
- The container floor is similarly approximated as a single flat plane at the
  minimum world-Y of the container's OBB vertices, with its footprint the XZ
  bounding rectangle of the container's 4 lowest-Y vertices. A heavily tilted
  container is out of scope.
- Support-region union area (when multiple supporting objects/floor overlap
  an object's footprint) is computed by rasterizing the axis-aligned
  rectangles onto their combined coordinate grid (coordinate compression) —
  exact for axis-aligned rectangles, no Monte Carlo / sampling error.
- `stability_margin_m` is the signed 2D distance from the object's COM (its
  OBB center's X,Z) to the nearest edge of the *merged bounding rectangle* of
  all contributing support regions (not the true, possibly non-convex union
  shape) — positive when the COM projection is inside that rectangle,
  negative outside. When an object has zero support (support_ratio == 0),
  there is no support rectangle to measure against, so we report a large
  negative sentinel (`FLOATING_MARGIN_SENTINEL_M`) rather than a real
  distance.

Complexity: O(n^2) worst case — every object is checked for support against
every other object (plus the container floor). Fine for hackathon scale
(5-20 objects); would need a spatial index to scale further.

Known failure modes / out of scope:
- No friction model: a box that would obviously be held in place by
  friction against a wall or neighbor is judged on footprint/COM alone.
- No dynamic tipping: does not simulate acceleration, vibration, or impact.
- Multi-point support of oddly shaped/rotated stacks is approximated via
  bounding rectangles (see above), which can overstate contact area for
  tilted objects and cannot represent true concave/L-shaped support unions.
- Does not model multi-body chain reactions (e.g. A destabilizing B which
  destabilizes C) — each object's support is evaluated independently.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from physics.geometry import OBB, obb_from, obb_vertices
from physics.schema import Scene

FLOATING_MARGIN_SENTINEL_M = -1.0e6

Rect = tuple[float, float, float, float]  # xmin, xmax, zmin, zmax


@dataclass
class SupportResult:
    object_id: str
    support_ratio: float
    stability_margin_m: float
    supporting_objects: list[str] = field(default_factory=list)
    floating: bool = False
    unstable: bool = False


def _face_rect(obb: OBB, lowest: bool) -> tuple[float, Rect]:
    """Return (face_y, xz_bounding_rect) for the bottom (lowest=True) or top
    face of `obb`, per the bounding-rectangle-of-4-extreme-vertices
    approximation documented in the module docstring."""
    verts = obb_vertices(obb)  # (8, 3)
    y = verts[:, 1]
    idx = np.argsort(y)[:4] if lowest else np.argsort(y)[-4:]
    face_y = float(y.min() if lowest else y.max())
    xs = verts[idx, 0]
    zs = verts[idx, 2]
    return face_y, (float(xs.min()), float(xs.max()), float(zs.min()), float(zs.max()))


def _rect_intersection(a: Rect, b: Rect) -> Rect | None:
    xmin = max(a[0], b[0])
    xmax = min(a[1], b[1])
    zmin = max(a[2], b[2])
    zmax = min(a[3], b[3])
    if xmin >= xmax or zmin >= zmax:
        return None
    return (xmin, xmax, zmin, zmax)


def _rect_area(r: Rect) -> float:
    return max(0.0, r[1] - r[0]) * max(0.0, r[3] - r[2])


def _union_area(rects: list[Rect]) -> float:
    """Exact union area of axis-aligned rectangles via coordinate
    compression (grid cell midpoint membership test)."""
    if not rects:
        return 0.0
    xs = sorted({r[0] for r in rects} | {r[1] for r in rects})
    zs = sorted({r[2] for r in rects} | {r[3] for r in rects})
    area = 0.0
    for i in range(len(xs) - 1):
        cx = (xs[i] + xs[i + 1]) / 2.0
        for j in range(len(zs) - 1):
            cz = (zs[j] + zs[j + 1]) / 2.0
            if any(r[0] <= cx <= r[1] and r[2] <= cz <= r[3] for r in rects):
                area += (xs[i + 1] - xs[i]) * (zs[j + 1] - zs[j])
    return area


def _merge_rect(rects: list[Rect]) -> Rect:
    return (
        min(r[0] for r in rects),
        max(r[1] for r in rects),
        min(r[2] for r in rects),
        max(r[3] for r in rects),
    )


def _signed_dist_to_rect(px: float, pz: float, r: Rect) -> float:
    xmin, xmax, zmin, zmax = r
    if xmin <= px <= xmax and zmin <= pz <= zmax:
        return min(px - xmin, xmax - px, pz - zmin, zmax - pz)
    dx = max(xmin - px, 0.0, px - xmax)
    dz = max(zmin - pz, 0.0, pz - zmax)
    return -float(np.hypot(dx, dz))


def check_support(
    scene: Scene,
    epsilon: float = 1e-3,
    floating_threshold: float = 0.05,
) -> list[SupportResult]:
    """Compute a `SupportResult` per object in `scene.objects` (same order).

    Assumes the scene is already valid (non-colliding, contained) — this
    function does not re-check that.

    `epsilon` (meters) is the floor/stacking contact tolerance: the max gap
    between an object's bottom-face Y and the supporting floor/object's
    top-face Y that still counts as "resting on" it. Deliberately larger
    (1e-3 = 1mm) than collision.py/containment.py's 1e-6 float-noise epsilon
    -- this one has to absorb real reconstruction/measurement noise between
    two independently-placed objects' faces, not just float64 rounding.
    """
    container_obb = obb_from(scene.container)
    floor_y, floor_rect = _face_rect(container_obb, lowest=True)

    obbs = {obj.id: obb_from(obj) for obj in scene.objects}
    bottoms = {oid: _face_rect(obb, lowest=True) for oid, obb in obbs.items()}
    tops = {oid: _face_rect(obb, lowest=False) for oid, obb in obbs.items()}

    results: list[SupportResult] = []
    for obj in scene.objects:
        obb = obbs[obj.id]
        bottom_y, bottom_rect = bottoms[obj.id]

        support_rects: list[Rect] = []
        supporting_objects: list[str] = []

        if abs(bottom_y - floor_y) <= epsilon:
            clipped = _rect_intersection(bottom_rect, floor_rect)
            if clipped is not None:
                support_rects.append(clipped)
                supporting_objects.append("container_floor")

        for other in scene.objects:
            if other.id == obj.id:
                continue
            top_y, top_rect = tops[other.id]
            if abs(bottom_y - top_y) > epsilon:
                continue
            clipped = _rect_intersection(bottom_rect, top_rect)
            if clipped is not None:
                support_rects.append(clipped)
                supporting_objects.append(other.id)

        bottom_area = _rect_area(bottom_rect)
        covered_area = _union_area(
            [_rect_intersection(r, bottom_rect) or (0, 0, 0, 0) for r in support_rects]
        )
        support_ratio = 0.0 if bottom_area <= 0 else min(1.0, covered_area / bottom_area)

        if support_rects:
            merged = _merge_rect(support_rects)
            margin = _signed_dist_to_rect(float(obb.center[0]), float(obb.center[2]), merged)
        else:
            margin = FLOATING_MARGIN_SENTINEL_M

        results.append(
            SupportResult(
                object_id=obj.id,
                support_ratio=support_ratio,
                stability_margin_m=margin,
                supporting_objects=supporting_objects,
                floating=support_ratio < floating_threshold,
                unstable=margin < 0,
            )
        )
    return results
