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

Complexity: O(1) per object (8 vertices, 3 axes), O(n) per scene (n objects).

Known failure modes / out of scope:
- Only object-vs-container is checked here; object-vs-object collisions are
  a separate module's job.
- Assumes convex box geometry — does not model non-box/irregular shapes.
- Does not account for soft/compressible items (e.g. squishable bags).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from physics.geometry import OBB, obb_from, obb_vertices
from physics.schema import Scene

# container-local axis index -> (negative wall name, positive wall name)
_WALL_NAMES = [("-x", "+x"), ("-y", "+y"), ("-z", "+z")]


@dataclass
class ContainmentResult:
    object_id: str
    contained: bool
    penetrating_vertices: list[np.ndarray] = field(default_factory=list)
    penetration_depth_m: float = 0.0
    violated_walls: list[str] = field(default_factory=list)


def check_no_duplicate_ids(scene: Scene) -> None:
    """Raise ValueError naming the offending id if any object id repeats,
    or if an object id collides with the container id."""
    seen: set[str] = {scene.container.id}
    for obj in scene.objects:
        if obj.id in seen:
            raise ValueError(f"duplicate object id: {obj.id!r}")
        seen.add(obj.id)


def check_containment(
    container_obb: OBB, object_obb: OBB, epsilon: float = 1e-6
) -> ContainmentResult:
    """Is `object_obb` fully inside `container_obb`?

    Projects all 8 world-space vertices of the object onto the container's
    local axes and compares against the container's half-extents. A vertex
    up to `epsilon` meters outside a wall still counts as contained
    (touching), anything beyond that is a penetration.
    """
    verts = obb_vertices(object_obb)  # (8, 3) world space
    local = (verts - container_obb.center) @ container_obb.axes  # (8, 3)

    penetrating_vertices: list[np.ndarray] = []
    max_depth = 0.0
    violated_walls: set[str] = set()

    for i in range(8):
        v_local = local[i]
        over = np.abs(v_local) - container_obb.half_extents  # (3,)
        if np.any(over > epsilon):
            penetrating_vertices.append(verts[i])
            for axis in range(3):
                if over[axis] > epsilon:
                    max_depth = max(max_depth, float(over[axis]))
                    neg_wall, pos_wall = _WALL_NAMES[axis]
                    violated_walls.add(pos_wall if v_local[axis] > 0 else neg_wall)

    contained = len(penetrating_vertices) == 0
    return ContainmentResult(
        object_id=object_obb.id,
        contained=contained,
        penetrating_vertices=penetrating_vertices,
        penetration_depth_m=max_depth,
        violated_walls=sorted(violated_walls),
    )


def check_scene_containment(scene: Scene, epsilon: float = 1e-6) -> list[ContainmentResult]:
    """Build the container OBB and every object OBB (via obb_from) and run
    check_containment for each object.

    Returns only the results for objects that are NOT contained (violations)
    — this is a FILTERED list, not "all results". An empty list means every
    object is fully contained. `epsilon` passes through to `check_containment`
    for every object (scene-wide wall-touch tolerance).
    """
    check_no_duplicate_ids(scene)
    container_obb = obb_from(scene.container)
    violations = []
    for obj in scene.objects:
        object_obb = obb_from(obj)
        result = check_containment(container_obb, object_obb, epsilon=epsilon)
        if not result.contained:
            violations.append(result)
    return violations
