"""Travel-semantics constraint checker.

Optional metadata-driven layer on top of `physics.geometry`. Does nothing if
every object's `Constraints` is left at defaults (all False / None) — it only
flags things an object opted into via `fragile`, `keep_upright`,
`cannot_support_weight`, `heavy`, or `orientation_lock`.

Checks:
  1. keep_upright: local Y axis (OBB.axes[:, 1]) must stay within
     `angle_tol_deg` (default 15) of world up [0, 1, 0]. -> LIQUID_NOT_UPRIGHT.
  2. orientation_lock:
       "this_side_up" -> same test as keep_upright.
       "flat_only"    -> local Y axis must be within tolerance of +Y or -Y
                         (object lying on its largest face, either way up).
       "horizontal"   -> local Y axis must be within tolerance of the XZ
                         plane (roughly perpendicular to world up).
     -> INVALID_ORIENTATION.
  3. cannot_support_weight: violated if nonzero mass rests on top of the
     object (weight-overlap below). -> FRAGILE_OBJECT_OVERLOADED.
  4. fragile (warning, not a violation): nonzero mass rests on top of the
     object, OR the object's XZ footprint overlaps/near-touches a `heavy`
     object's footprint at roughly the same height. -> FRAGILE_LOAD.
  5. heavy resting on top of anything (informational, always emitted when it
     happens, independent of the object underneath's constraints).
     -> HEAVY_ON_TOP.

Weight-overlap approximation ("what rests on X"): for every ordered pair
(X, Y) with Y != X, Y counts as resting on X if Y's OBB lowest-Y extent is
within `contact_eps_m` (default 0.02 m) of X's OBB highest-Y extent, AND
their axis-aligned XZ bounding rectangles (derived from each OBB's 8
vertices, i.e. already accounting for rotation) overlap by any nonzero
amount. This is O(n^2) over the scene's objects — fine for the object counts
a suitcase-packing scene actually has. "Adjacent" for the fragile/heavy
check reuses the same XZ-rectangle-overlap test, without the height
requirement, as a footprint-proximity approximation — it deliberately does
not measure true 3D contact/side-adjacency.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from physics.geometry import obb_from, obb_vertices
from physics.schema import Scene

WORLD_UP = np.array([0.0, 1.0, 0.0])
DEFAULT_ANGLE_TOL_DEG = 15.0
DEFAULT_CONTACT_EPS_M = 0.02


@dataclass
class ConstraintViolation:
    type: str
    object_id: str
    details: dict = field(default_factory=dict)


@dataclass
class ConstraintWarning:
    type: str
    object_id: str
    details: dict = field(default_factory=dict)


def _up_axis_tilt_deg(local_up: np.ndarray) -> float:
    """Angle in degrees between an object's local up axis and world up."""
    cos = np.clip(np.dot(local_up, WORLD_UP) / np.linalg.norm(local_up), -1.0, 1.0)
    return math.degrees(math.acos(cos))


def _xz_rect(vertices: np.ndarray) -> tuple[float, float, float, float]:
    """(min_x, max_x, min_z, max_z) axis-aligned rectangle from 8 OBB verts."""
    return vertices[:, 0].min(), vertices[:, 0].max(), vertices[:, 2].min(), vertices[:, 2].max()


def _rects_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    ax0, ax1, az0, az1 = a
    bx0, bx1, bz0, bz1 = b
    return ax0 < bx1 and bx0 < ax1 and az0 < bz1 and bz0 < az1


def check_constraints(
    scene: Scene,
    angle_tol_deg: float = DEFAULT_ANGLE_TOL_DEG,
    contact_eps_m: float = DEFAULT_CONTACT_EPS_M,
) -> tuple[list[ConstraintViolation], list[ConstraintWarning]]:
    violations: list[ConstraintViolation] = []
    warnings: list[ConstraintWarning] = []

    objects = scene.objects
    obbs = {o.id: obb_from(o) for o in objects}
    verts = {oid: obb_vertices(obb) for oid, obb in obbs.items()}
    rects = {oid: _xz_rect(v) for oid, v in verts.items()}
    y_min = {oid: v[:, 1].min() for oid, v in verts.items()}
    y_max = {oid: v[:, 1].max() for oid, v in verts.items()}

    # supported_weight_kg[X] = total mass of objects resting on top of X.
    supported_weight: dict[str, float] = {o.id: 0.0 for o in objects}
    resting_on: dict[str, list[str]] = {o.id: [] for o in objects}
    for y in objects:
        for x in objects:
            if x.id == y.id:
                continue
            if abs(y_min[y.id] - y_max[x.id]) <= contact_eps_m and _rects_overlap(rects[x.id], rects[y.id]):
                supported_weight[x.id] += y.mass_kg
                resting_on[y.id].append(x.id)

    by_id = {o.id: o for o in objects}

    for obj in objects:
        c = obj.constraints
        local_up = obbs[obj.id].axes[:, 1]
        tilt = _up_axis_tilt_deg(local_up)

        if c.keep_upright and tilt > angle_tol_deg:
            violations.append(
                ConstraintViolation(
                    type="LIQUID_NOT_UPRIGHT",
                    object_id=obj.id,
                    details={"tilt_deg": tilt, "tolerance_deg": angle_tol_deg},
                )
            )

        lock = c.orientation_lock
        if lock == "this_side_up":
            if tilt > angle_tol_deg:
                violations.append(
                    ConstraintViolation(
                        type="INVALID_ORIENTATION",
                        object_id=obj.id,
                        details={"lock": lock, "tilt_deg": tilt, "tolerance_deg": angle_tol_deg},
                    )
                )
        elif lock == "flat_only":
            angle_to_axis = min(tilt, 180.0 - tilt)  # distance to nearer of +Y/-Y
            if angle_to_axis > angle_tol_deg:
                violations.append(
                    ConstraintViolation(
                        type="INVALID_ORIENTATION",
                        object_id=obj.id,
                        details={"lock": lock, "tilt_deg": angle_to_axis, "tolerance_deg": angle_tol_deg},
                    )
                )
        elif lock == "horizontal":
            angle_to_plane = abs(90.0 - tilt)  # distance from the XZ plane (90 deg from up)
            if angle_to_plane > angle_tol_deg:
                violations.append(
                    ConstraintViolation(
                        type="INVALID_ORIENTATION",
                        object_id=obj.id,
                        details={"lock": lock, "tilt_deg": angle_to_plane, "tolerance_deg": angle_tol_deg},
                    )
                )

        weight_on_top = supported_weight[obj.id]
        if c.cannot_support_weight and weight_on_top > 0:
            violations.append(
                ConstraintViolation(
                    type="FRAGILE_OBJECT_OVERLOADED",
                    object_id=obj.id,
                    details={"supported_weight_kg": weight_on_top},
                )
            )

        if c.fragile:
            adjacent_heavy = any(
                by_id[other].constraints.heavy and _rects_overlap(rects[obj.id], rects[other])
                for other in rects
                if other != obj.id
            )
            if weight_on_top > 0 or adjacent_heavy:
                warnings.append(
                    ConstraintWarning(
                        type="FRAGILE_LOAD",
                        object_id=obj.id,
                        details={"supported_weight_kg": weight_on_top, "adjacent_heavy": adjacent_heavy},
                    )
                )

        if c.heavy and resting_on[obj.id]:
            warnings.append(
                ConstraintWarning(
                    type="HEAVY_ON_TOP",
                    object_id=obj.id,
                    details={"resting_on": resting_on[obj.id]},
                )
            )

    return violations, warnings
