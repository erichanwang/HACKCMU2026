"""Travel-semantics constraint checker.

Optional metadata-driven layer on top of `physics.scene_geometry`. Does
nothing if every object's `Constraints` is left at defaults (all False /
None) -- it only flags things an object opted into via `fragile`,
`keep_upright`, `cannot_support_weight`, `heavy`, or `orientation_lock`.

Checks:
  1. keep_upright: local Y axis (`geom.axes[:, :, 1]`) must stay within
     `angle_tol_deg` (default 15) of world up [0, 1, 0]. -> LIQUID_NOT_UPRIGHT.
  2. orientation_lock:
       "this_side_up" -> same test as keep_upright.
       "flat_only"    -> local Y axis must be within tolerance of +Y or -Y
                         (object lying on its largest face, either way up).
       "horizontal"   -> local Y axis must be within tolerance of the XZ
                         plane (roughly perpendicular to world up).
     -> INVALID_ORIENTATION.
  3. cannot_support_weight: violated if nonzero TRANSITIVE load rests on top
     of the object (below). -> FRAGILE_OBJECT_OVERLOADED.
  4. fragile (warning, not a violation): nonzero transitive load rests on
     top of the object, OR the object's XZ footprint overlaps a `heavy`
     object's footprint at roughly the same height -- "same height" being
     Y ranges that overlap within `contact_eps_m`, so a heavy item two
     shelves up is not "adjacent" to a fragile one on the floor.
     -> FRAGILE_LOAD.
  5. heavy resting on top of anything (informational, always emitted when it
     happens, independent of the object underneath's constraints).
     -> HEAVY_ON_TOP.

Contact topology ("what rests on what") comes from the shared
`scene_geometry.resting_pairs`: an ordered (top, bottom, xz_overlap_area)
triple for every pair where top's lowest-Y is within `contact_eps_m` of
bottom's highest-Y and their AABB footprints overlap with positive area.
Rotation-aware to the extent an AABB is (exact for yaw, conservative
otherwise) -- see `scene_geometry.xz_overlap_area`. Building this is O(n^2),
a single vectorized broadcast over the scene's objects.

Load model -- transitive, area-weighted propagation:
A resting DAG can stack more than one level deep (a shoe on a toiletry bag
on a laptop): the laptop is loaded by the *whole* stack above it, not just
the toiletry bag directly touching it. We compute, per object x:

    load[x] = mass[x] + sum_{y rests on x} load[y] * w_yx
    w_yx = area_yx / sum_{z: y rests on z} area_yz
    supported_weight_kg[x] = load[x] - mass[x]

i.e. each object's accumulated load (its own mass plus everything piled on
it) is split among *its own* direct supporters in proportion to contact
overlap area -- a single supporter takes 100% of it, two equal-area
supporters split it 50/50, etc. This collapses to the old direct-sum
definition for single-level stacks (one supporter, w = 1), so existing
single-level results are unchanged.

Computed by processing objects top-down (descending `aabb_min[:, 1]`, so a
resting DAG's leaves are visited before its roots) and, for each object in
that order, pushing its already-fully-accumulated `load` down onto its own
direct supporters. One pass, O(E) over the resting-pair edges after an
O(n log n) sort -- dominated by the O(n^2) topology build above. We also
keep the old direct definition (`direct_weight_kg` = sum of mass of objects
*directly* on top, no propagation) alongside the transitive one in the
violation/warning details, since it's still a useful "what's touching this"
number for a renderer.

`HEAVY_ON_TOP.details.load_path`: from the heavy object down to the
floor-level (no-supporters) object, following the heaviest-loaded supporting
edge at each step -- i.e. at each node, the direct supporter receiving the
largest share of that node's load (equivalent to the largest-area supporter,
since a node's load is fixed when comparing its own supporters).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from physics.scene_geometry import SceneGeometry, precompute, resting_pairs, xz_overlap_area
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


def _adjacent_at_same_height(geom: SceneGeometry, i: int, j: int, tol: float) -> bool:
    """Are i and j side by side -- footprints overlapping AND Y ranges
    overlapping within `tol`? The XZ overlap alone is a shadow test: without
    the height gate a heavy case on the top layer counts as "adjacent" to a
    fragile one on the floor directly below it, which is exactly the pair the
    warning is not about. Mirrored in `physics.incremental._adjacent_heavy`."""
    if xz_overlap_area(geom, i, j) <= 0.0:
        return False
    lo, hi = geom.aabb_min[:, 1], geom.aabb_max[:, 1]
    return lo[i] - tol <= hi[j] and lo[j] - tol <= hi[i]


def _up_axis_tilt_deg(local_up: np.ndarray) -> float:
    """Angle in degrees between an object's local up axis and world up."""
    cos = np.clip(np.dot(local_up, WORLD_UP) / np.linalg.norm(local_up), -1.0, 1.0)
    return math.degrees(math.acos(cos))


def _up_axis_cosines(axes: np.ndarray) -> list[float]:
    """`_up_axis_tilt_deg`'s clipped cosine for every object at once.

    Dotting a local up axis with world up (0, 1, 0) just picks that column's
    y component, and the columns of a rotation matrix are unit length -- but
    only to within ~1e-16, and `acos` is steep near 1, so the division by the
    norm is kept: it is what makes this bit-for-bit equal to the per-object
    path. Only the (cheap) `acos` is left to the caller, which needs it solely
    for objects that opted into an orientation constraint."""
    up = axes[:, :, 1]
    return np.clip(up[:, 1] / np.linalg.norm(up, axis=1), -1.0, 1.0).tolist()


def check_constraints(
    scene: Scene,
    angle_tol_deg: float = DEFAULT_ANGLE_TOL_DEG,
    contact_eps_m: float = DEFAULT_CONTACT_EPS_M,
    geom: SceneGeometry | None = None,
) -> tuple[list[ConstraintViolation], list[ConstraintWarning]]:
    violations: list[ConstraintViolation] = []
    warnings: list[ConstraintWarning] = []

    if geom is None:
        geom = precompute(scene)
    objects = geom.objects
    n = geom.n
    if n == 0:
        return violations, warnings

    ids = geom.ids
    masses = geom.masses.tolist()  # plain floats: same IEEE arithmetic, no numpy scalars
    pairs = resting_pairs(geom, contact_eps_m)  # [(top_idx, bottom_idx, area), ...]

    direct_weight = [0.0] * n  # sum of mass of objects DIRECTLY on top of x (old definition)
    resting_on_direct: list[list[int]] = [[] for _ in range(n)]  # top -> [direct supporter idx, ...]
    supporters: list[list[tuple[int, float]]] = [[] for _ in range(n)]  # top -> [(bottom idx, area), ...]
    total_support_area = [0.0] * n  # per top object, sum of area over its own direct supporters

    for top, bottom, area in pairs:
        direct_weight[bottom] += masses[top]
        resting_on_direct[top].append(bottom)
        supporters[top].append((bottom, area))
        total_support_area[top] += area

    # Transitive load: visit top-down (highest aabb_min first) so that by the
    # time a node is visited, everything resting on it has already pushed its
    # share into it -- then push this node's now-final load onto its own
    # direct supporters, split by area fraction.
    load = list(masses)
    for y in np.argsort(-geom.aabb_min[:, 1]).tolist():
        area_total = total_support_area[y]
        if area_total <= 0.0:
            continue
        for x, area in supporters[y]:
            load[x] += load[y] * (area / area_total)

    supported_weight = [ld - m for ld, m in zip(load, masses)]

    def _load_path(start: int) -> list[str]:
        path = [ids[start]]
        seen = {start}
        cur = start
        while supporters[cur]:
            nxt, _ = max(supporters[cur], key=lambda t: t[1])
            if nxt in seen:
                break
            path.append(ids[nxt])
            seen.add(nxt)
            cur = nxt
        return path

    cos_up = _up_axis_cosines(geom.axes)
    heavy_idx = [j for j, o in enumerate(objects) if o.constraints.heavy]

    for i, obj in enumerate(objects):
        c = obj.constraints
        lock = c.orientation_lock
        # Only orientation constraints read the tilt, so only they pay for it.
        tilt = (
            math.degrees(math.acos(cos_up[i]))
            if c.keep_upright or lock is not None
            else 0.0
        )

        if c.keep_upright and tilt > angle_tol_deg:
            violations.append(
                ConstraintViolation(
                    type="LIQUID_NOT_UPRIGHT",
                    object_id=obj.id,
                    details={"tilt_deg": tilt, "tolerance_deg": angle_tol_deg},
                )
            )

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

        if c.cannot_support_weight and supported_weight[i] > 0:
            violations.append(
                ConstraintViolation(
                    type="FRAGILE_OBJECT_OVERLOADED",
                    object_id=obj.id,
                    details={
                        "supported_weight_kg": float(supported_weight[i]),
                        "direct_weight_kg": float(direct_weight[i]),
                    },
                )
            )

        if c.fragile:
            adjacent_heavy = any(
                _adjacent_at_same_height(geom, i, j, contact_eps_m)
                for j in heavy_idx
                if j != i
            )
            if supported_weight[i] > 0 or adjacent_heavy:
                warnings.append(
                    ConstraintWarning(
                        type="FRAGILE_LOAD",
                        object_id=obj.id,
                        details={
                            "supported_weight_kg": float(supported_weight[i]),
                            "direct_weight_kg": float(direct_weight[i]),
                            "adjacent_heavy": adjacent_heavy,
                        },
                    )
                )

        if c.heavy and resting_on_direct[i]:
            warnings.append(
                ConstraintWarning(
                    type="HEAVY_ON_TOP",
                    object_id=obj.id,
                    details={
                        "resting_on": [ids[b] for b in resting_on_direct[i]],
                        "load_path": _load_path(i),
                    },
                )
            )

    return violations, warnings
