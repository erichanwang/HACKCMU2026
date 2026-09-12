"""Object-in-container containment checks.

Assumptions:
- Both container and object are convex OBBs (oriented bounding boxes) built
  via `physics.geometry.obb_from` / `OBB`. Non-box geometry is not modeled.
- Coordinate system matches physics.schema: X=right, Y=up, Z=forward, meters,
  quaternion (x, y, z, w).
- The container may itself be rotated/positioned arbitrarily in world space
  (not just axis-aligned at the origin) — we never assume container rotation
  is identity, even though that is the common case.
- "Containment" is tested by projecting all 8 world-space vertices of the
  object OBB into the container's local frame and comparing against the
  container's half-extents on each local axis. Checking only the object's
  center is NOT sufficient: a small rotation can poke a corner through a
  wall while the center stays well inside (see tests).
- epsilon is a tolerance in meters: a vertex within `epsilon` of a container
  wall counts as inside/touching, not penetrating. This absorbs floating
  point noise and lets "flush against the wall" packing count as valid.

Complexity: one batched op. `_containment_arrays` does a single einsum
projecting every vertex of every object into the container's local frame
(O(n) for n objects, same as before) plus a fixed-size loop over the 3
container axes (not over n) to pull out per-wall depths; `check_containment`
(one object) and `check_scene_containment` (n objects, via `SceneGeometry`)
both call this one core -- there is exactly one implementation of the math.

`ContainmentResult.per_wall_depth_m` gives the max overshoot per violated
wall (container-local `"-x"/"+x"/"-y"/"+y"/"-z"/"+z"`); `penetration_depth_m`
is `max(per_wall_depth_m.values(), default=0.0)` and `violated_walls` is
`sorted(per_wall_depth_m)` -- both derived, so callers that only knew the old
two fields keep working unchanged.

Known failure modes / out of scope:
- Only object-vs-container is checked here; object-vs-object collisions are
  a separate module's job.
- Assumes convex box geometry — does not model non-box/irregular shapes.
- Does not account for soft/compressible items (e.g. squishable bags).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np

from physics.geometry import OBB, obb_vertices
from physics.scene_geometry import (  # noqa: F401 (check_no_duplicate_ids re-exported)
    SceneGeometry,
    check_no_duplicate_ids,
    precompute,
)
from physics.schema import Scene


# container-local axis index -> (negative wall name, positive wall name)
_WALL_NAMES = [("-x", "+x"), ("-y", "+y"), ("-z", "+z")]
# flat wall order matching the columns of `_containment_arrays`'s wall_depth
_WALL_ORDER = [name for pair in _WALL_NAMES for name in pair]


@dataclass
class ContainmentResult:
    object_id: str
    contained: bool
    penetrating_vertices: list[np.ndarray] = field(default_factory=list)
    penetration_depth_m: float = 0.0
    violated_walls: list[str] = field(default_factory=list)
    per_wall_depth_m: dict[str, float] = field(default_factory=dict)



def _containment_arrays(
    vertices: np.ndarray,  # (n, 8, 3) world-space object corners
    container_center: np.ndarray,  # (3,)
    container_axes: np.ndarray,  # (3, 3), columns = container's world axes
    container_half_extents: np.ndarray,  # (3,)
    epsilon: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Batched containment core shared by every caller.

    One einsum projects every vertex of every object into the container's
    local frame; a fixed-size (3-axis) loop of masked reductions over the 8
    vertices then pulls out, per object, the max overshoot on each of the 6
    walls. The loop is over container axes (always 3), never over n.

    Returns:
      wall_depth: (n, 6) float, columns in `_WALL_ORDER` order, `-inf` where
        that wall isn't violated (beyond `epsilon`) for that object.
      vertex_penetrating: (n, 8) bool, True where that vertex is outside any
        wall by more than `epsilon`.
    """
    local = np.einsum("nvj,jk->nvk", vertices - container_center, container_axes)  # (n,8,3)
    over = np.abs(local) - container_half_extents  # (n, 8, 3)
    violated = over > epsilon  # (n, 8, 3)
    pos_side = local > 0  # (n, 8, 3)

    n = vertices.shape[0]
    wall_depth = np.full((n, 6), -np.inf)
    for axis in range(3):
        axis_over = over[:, :, axis]
        axis_viol = violated[:, :, axis]
        axis_pos = pos_side[:, :, axis]
        wall_depth[:, 2 * axis] = np.where(axis_viol & ~axis_pos, axis_over, -np.inf).max(axis=1)
        wall_depth[:, 2 * axis + 1] = np.where(axis_viol & axis_pos, axis_over, -np.inf).max(axis=1)

    vertex_penetrating = violated.any(axis=2)  # (n, 8)
    return wall_depth, vertex_penetrating


def _build_result(
    object_id: str, verts: np.ndarray, wall_depth: np.ndarray, vertex_penetrating: np.ndarray
) -> ContainmentResult:
    """Assemble one ContainmentResult from one row of `_containment_arrays`'s
    output. `verts` is (8, 3), `wall_depth` is (6,), `vertex_penetrating` is
    (8,) bool."""
    per_wall = {
        _WALL_ORDER[w]: float(wall_depth[w]) for w in range(6) if np.isfinite(wall_depth[w])
    }
    penetrating_vertices = [verts[v] for v in np.nonzero(vertex_penetrating)[0]]
    return ContainmentResult(
        object_id=object_id,
        contained=len(penetrating_vertices) == 0,
        penetrating_vertices=penetrating_vertices,
        penetration_depth_m=max(per_wall.values(), default=0.0),
        violated_walls=sorted(per_wall),
        per_wall_depth_m=per_wall,
    )


def check_containment(
    container_obb: OBB, object_obb: OBB, epsilon: float = 1e-6
) -> ContainmentResult:
    """Is `object_obb` fully inside `container_obb`?

    Projects all 8 world-space vertices of the object onto the container's
    local axes and compares against the container's half-extents. A vertex
    up to `epsilon` meters outside a wall still counts as contained
    (touching), anything beyond that is a penetration. Runs the same batched
    core as `check_scene_containment`, just with n=1.
    """
    verts = obb_vertices(object_obb)[None]  # (1, 8, 3)
    wall_depth, vertex_penetrating = _containment_arrays(
        verts, container_obb.center, container_obb.axes, container_obb.half_extents, epsilon
    )
    return _build_result(object_obb.id, verts[0], wall_depth[0], vertex_penetrating[0])


def check_scene_containment(
    scene: Scene, epsilon: float = 1e-6, geom: SceneGeometry | None = None
) -> list[ContainmentResult]:
    """Containment for every object in `scene`, computed in one batched pass.

    Returns only the results for objects that are NOT contained (violations)
    — this is a FILTERED list, not "all results". An empty list means every
    object is fully contained (including the 0-object scene). `epsilon` is
    the scene-wide wall-touch tolerance.

    `geom` lets a caller that already ran `physics.scene_geometry.precompute`
    pass it in instead of paying for it twice; when omitted, `precompute` is
    called here (which already runs `check_no_duplicate_ids` and `obb_from`
    per object, so malformed scenes still raise the same `ValueError`s as
    before).
    """
    if geom is None:
        geom = precompute(scene)

    container = geom.container_obb
    wall_depth, vertex_penetrating = _containment_arrays(
        geom.vertices, container.center, container.axes, container.half_extents, epsilon
    )
    violator_rows = np.nonzero(vertex_penetrating.any(axis=1))[0]
    return [
        _build_result(geom.ids[i], geom.vertices[i], wall_depth[i], vertex_penetrating[i])
        for i in violator_rows
    ]
