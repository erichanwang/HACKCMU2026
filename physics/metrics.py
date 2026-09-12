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
"""
from __future__ import annotations

import numpy as np

from physics.scene_geometry import SceneGeometry


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
    if total_mass_kg > 0.0:
        center_of_mass = (geom.masses[:, None] * geom.centers).sum(axis=0) / total_mass_kg
    else:
        # All-massless scene (e.g. solver obstacles, mass_kg=0): a mass-weighted COM is
        # undefined, so fall back to the container centre instead of dividing by zero.
        center_of_mass = container.center
    com_offset_m = (center_of_mass - container.center) @ container.axes  # world -> container-local

    object_volumes_m3 = 8.0 * np.prod(geom.half_extents, axis=1)  # (n,)
    fill_ratio = float(object_volumes_m3.sum() / container_volume_m3)

    # Wall clearance: same vertex-into-container-frame projection as
    # physics.containment, but reported as remaining room (can go negative)
    # rather than filtered to violations only.
    local = np.einsum("nvj,jk->nvk", geom.vertices - container.center, container.axes)  # (n,8,3)
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
