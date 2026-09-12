"""Scene-level packing metrics -- objective terms for the packing solver.

`scene_metrics(geom)` takes an already-built `physics.scene_geometry.
SceneGeometry` and returns a plain dict of floats/lists/dicts (no numpy
scalars, so it's directly `json.dumps`-able): overall mass balance
(`total_mass_kg`, `center_of_mass`, `com_offset_m`), how full the container
is (`fill_ratio`), and per-object spatial stats (`per_object`): how much room
is left before each wall (`wall_clearance_m`), how close the nearest other
object is (`nearest_neighbor_gap_m` / `nearest_neighbor_id`), height off the
floor, footprint, and volume.

Units: meters and kilograms everywhere, matching physics.schema.

`com_offset_m` is expressed in the CONTAINER's local axes (not world), so a
solver can push mass toward a fixed side of the container (e.g. the wheel
end) regardless of how the container itself is rotated/placed in world space
-- same convention `physics.containment` uses for wall projections.

`nearest_neighbor_gap_m` is AABB-based (per axis `max(lo_j-hi_i, lo_i-hi_j)`,
max over axes, clamped to >=0, then min over neighbours) -- conservative
(an under-estimate of true clearance) for rotated boxes, since a rotated
OBB's AABB is looser than the box itself. 0 when AABBs overlap. `None` when
there is no other object to compare against.

Complexity: O(n) for the mass/fill terms and per-object wall clearance (one
einsum over (n, 8, 3), same projection `physics.containment` uses); O(n^2)
for nearest-neighbour gaps (one (n, n, 3) broadcast) -- fine at hackathon
scale (5-20 objects), same tradeoff as `scene_geometry.aabb_candidate_pairs`.

`plan_quality(geom)` -- separate entry point, PLAN-quality rather than
solver-objective terms: how the packed bag behaves when a human picks it up,
tilts it onto its wheels and throws it into an overhead bin. Also plain
JSON-dumpable numbers, every one with a unit and a range:

| key | unit | range | meaning |
|---|---|---|---|
| `up_axis` / `long_axis` | -- | "x"/"y"/"z" | which container-local axis is up, and which horizontal one is the long (wheels <-> handle) one. A square footprint ties; the lower axis index wins |
| `com_height_fraction` | -- | 0..1 | CoM height above the container's base plane / container height. 0 = on the floor, 1 = in the lid. Outside 0..1 only if objects stick out of the bag |
| `com_lateral_offset_m` | m | >= 0 | horizontal distance from the CoM to the centre of the base |
| `com_long_axis_fraction` | -- | 0..1 | where the CoM sits along the long horizontal axis: 0 = the local -end, 1 = the +end. The caller knows which end has the wheels; the geometry does not |
| `tip_over_margin_deg[wall]` | deg | 0..90 | how far the bag can tilt about that bottom edge before the CoM crosses it. 0 = already past the edge, 90 = cannot topple that way |
| `min_tip_over_margin_deg` | deg | 0..90 | worst of the four. The honest "will it stand up": < ~10 deg topples when nudged |
| `packed_envelope_m3` | m^3 | >= 0 | volume of the container-local AABB around everything packed |
| `enclosed_void_m3` | m^3 | >= 0 | empty space trapped INSIDE that envelope -- the gaps you could squeeze out |
| `free_headroom_m3` | m^3 | any | container volume - envelope: clear space outside the packed block (negative if something overhangs the container) |
| `compactness` | -- | 0..1 | occupied volume / envelope volume. 1 = solid brick, 0.5 = half the used region is air |
| `mass_above_fragile_kg[id]` | kg | >= 0 | for each fragile item, the mass whose vertical column passes over it |

Assumptions and caveats, stated rather than hidden:
- Tip-over treats the container's own base as the ground plane, i.e. it
  assumes the container's up axis really is (roughly) world up -- true for
  every container this pipeline builds. A container tipped in world space
  gets a base that is not horizontal and these angles stop meaning anything.
- No friction, no dynamics: the tip-over angle is the static geometric one
  (`atan2(distance_to_edge, com_height)`), the same modelling level as
  `physics.support`.
- `enclosed_void_m3` / `compactness` use the sum of each object's own OBB
  volume as "occupied", exactly like `fill_ratio` -- an OVERLAPPING (invalid)
  scene double-counts the overlap, so compactness can exceed 1 there. These
  are numbers for a validated scene.
- `mass_above_fragile_kg` is a vertical-column model, not the resting graph:
  every object whose lowest point is at or above the fragile item's top
  contributes `mass * (XZ overlap with the fragile item / its own footprint)`.
  It therefore counts weight two layers up (which does press down through
  whatever is between) and it is AABB-based, so the overlap is exact for
  yaw-only rotation and an over-estimate otherwise. Compare
  `physics.constraints`' `supported_weight_kg`, which is the contact-graph
  answer to a different question ("what is literally resting on this").

Complexity: O(n) plus O(n) per fragile item.
"""
from __future__ import annotations

import math

import numpy as np

from physics.scene_geometry import (
    SceneGeometry,
    container_local_vertices,
    container_up_axis,
    xz_overlap_area,
)

AXIS_NAMES = ("x", "y", "z")
# An object whose lowest point is within this of a fragile item's top counts as
# sitting on it (float noise / slight compression), matching support.py's contact eps.
ABOVE_EPS_M = 1e-3


def _center_of_mass(geom: SceneGeometry) -> np.ndarray:
    """Mass-weighted centroid of the object OBB centres (uniform density, same
    assumption as support.py). Falls back to the container centre for an empty
    or all-massless scene (e.g. solver obstacles, mass_kg=0), where a
    mass-weighted centroid is undefined, instead of dividing by zero."""
    total = float(geom.masses.sum()) if geom.n else 0.0
    if total <= 0.0:
        return geom.container_obb.center
    return (geom.masses[:, None] * geom.centers).sum(axis=0) / total


def scene_metrics(geom: SceneGeometry) -> dict:
    container = geom.container_obb
    container_volume_m3 = float(8.0 * np.prod(container.half_extents))
    n = geom.n

    if n == 0:
        return {
            "total_mass_kg": 0.0,
            "center_of_mass": container.center.tolist(),
            "com_offset_m": [0.0, 0.0, 0.0],
            "fill_ratio": 0.0,
            "per_object": {},
        }

    total_mass_kg = float(geom.masses.sum())
    center_of_mass = _center_of_mass(geom)
    com_offset_m = (center_of_mass - container.center) @ container.axes  # world -> container-local

    object_volumes_m3 = 8.0 * np.prod(geom.half_extents, axis=1)  # (n,)
    fill_ratio = float(object_volumes_m3.sum() / container_volume_m3)

    # Wall clearance: same vertex-into-container-frame projection as
    # physics.containment, but reported as remaining room (can go negative)
    # rather than filtered to violations only.
    local = container_local_vertices(geom)  # (n,8,3)
    wall_clearance_m = (container.half_extents - np.abs(local)).min(axis=(1, 2))  # (n,)

    # Nearest-neighbour AABB gap: (n, n, 3) broadcast, then max over the 3
    # axes (per pair), then min over neighbours (per object).
    lo, hi = geom.aabb_min, geom.aabb_max
    gap_axis = np.maximum(lo[None, :, :] - hi[:, None, :], lo[:, None, :] - hi[None, :, :])
    gap = np.maximum(gap_axis.max(axis=2), 0.0)  # (n, n)
    np.fill_diagonal(gap, np.inf)

    footprint_area_m2 = (geom.aabb_max[:, 0] - geom.aabb_min[:, 0]) * (
        geom.aabb_max[:, 2] - geom.aabb_min[:, 2]
    )
    height_above_floor_m = geom.aabb_min[:, 1] - geom.container_floor_y

    has_neighbour = n > 1
    per_object = {}
    for i, oid in enumerate(geom.ids):
        nn_idx = int(np.argmin(gap[i])) if has_neighbour else None
        per_object[oid] = {
            "wall_clearance_m": float(wall_clearance_m[i]),
            "nearest_neighbor_gap_m": float(gap[i, nn_idx]) if has_neighbour else None,
            "nearest_neighbor_id": geom.ids[nn_idx] if has_neighbour else None,
            "height_above_floor_m": float(height_above_floor_m[i]),
            "footprint_area_m2": float(footprint_area_m2[i]),
            "volume_m3": float(object_volumes_m3[i]),
        }

    return {
        "total_mass_kg": total_mass_kg,
        "center_of_mass": center_of_mass.tolist(),
        "com_offset_m": com_offset_m.tolist(),
        "fill_ratio": fill_ratio,
        "per_object": per_object,
    }


def _mass_above_fragile(geom: SceneGeometry) -> dict:
    """{fragile object id: kg of mass whose vertical column passes over it}.
    See the module docstring for the model and its caveats."""
    fragile = [
        i for i, o in enumerate(geom.objects)
        if o.constraints.fragile or o.constraints.cannot_support_weight
    ]
    if not fragile:
        return {}
    span = geom.aabb_max - geom.aabb_min
    footprint = span[:, 0] * span[:, 2]  # (n,) XZ AABB area, > 0 (dimensions are > 0)
    out = {}
    for i in fragile:
        above = np.nonzero(geom.aabb_min[:, 1] >= geom.aabb_max[i, 1] - ABOVE_EPS_M)[0]
        out[geom.ids[i]] = float(sum(
            geom.masses[j] * xz_overlap_area(geom, i, int(j)) / footprint[j]
            for j in above if j != i
        ))
    return out


def plan_quality(geom: SceneGeometry) -> dict:
    """Plan-quality metrics for a validated scene: is this bag pleasant to
    carry, does it stand up, what is above the camera, and is the empty space
    one usable pocket or scattered gaps? See the module docstring for every
    field's unit, range, and modelling caveat."""
    container = geom.container_obb
    half = container.half_extents
    up, up_sign = container_up_axis(geom)
    horizontal = [k for k in range(3) if k != up]
    long_axis = max(horizontal, key=lambda k: half[k])

    com_local = (_center_of_mass(geom) - container.center) @ container.axes
    com_height_m = max(0.0, float(com_local[up] * up_sign + half[up]))  # above the base plane

    tip_over_margin_deg = {}
    for k in horizontal:
        for sign in (1.0, -1.0):
            # distance the CoM must travel to cross that bottom edge
            to_edge = max(0.0, float(half[k] - sign * com_local[k]))
            name = ("+" if sign > 0 else "-") + AXIS_NAMES[k]
            tip_over_margin_deg[name] = math.degrees(math.atan2(to_edge, com_height_m))

    occupied_m3 = float((8.0 * np.prod(geom.half_extents, axis=1)).sum())
    if geom.n:
        local = container_local_vertices(geom)
        envelope_m3 = float(np.prod(local.max(axis=(0, 1)) - local.min(axis=(0, 1))))
    else:
        envelope_m3 = 0.0

    return {
        "up_axis": AXIS_NAMES[up],
        "long_axis": AXIS_NAMES[long_axis],
        "com_height_fraction": com_height_m / (2.0 * float(half[up])),
        "com_lateral_offset_m": float(np.hypot(com_local[horizontal[0]], com_local[horizontal[1]])),
        "com_long_axis_fraction": float(
            (com_local[long_axis] + half[long_axis]) / (2.0 * half[long_axis])
        ),
        "tip_over_margin_deg": tip_over_margin_deg,
        "min_tip_over_margin_deg": min(tip_over_margin_deg.values()),
        "packed_envelope_m3": envelope_m3,
        "enclosed_void_m3": max(0.0, envelope_m3 - occupied_m3),
        "free_headroom_m3": float(8.0 * np.prod(half)) - envelope_m3,
        "compactness": (occupied_m3 / envelope_m3) if envelope_m3 > 0.0 else 0.0,
        "mass_above_fragile_kg": _mass_above_fragile(geom),
    }
