"""Verifies the packer3d <-> physics-schema coordinate bridge (packer3d/physics_bridge.py).

The quaternion formula below is copied verbatim from the HackCMU repo's
`physics/geometry.py::quat_to_matrix` (not imported, so packer3d stays dependency-free) --
this is the exact function the physics/renderer side will use to turn our emitted quaternion
back into a rotation matrix, so round-tripping through it is the real compatibility check.
"""
import math

import numpy as np
import pytest

from packer3d import (Container, Item, OptimizerConfig, ObjectiveWeights, load_scenario,
                      pack_optimized, verify)
from packer3d.physics_bridge import (R_AXES, matrix_to_quaternion, physics_container_dict,
                                     physics_object_dims, to_physics_placements)


def quat_to_matrix(q):  # verbatim copy of physics/geometry.py's quat_to_matrix
    x, y, z, w = q
    n = x * x + y * y + z * z + w * w
    assert abs(n - 1.0) < 1e-6, f"not unit length: {q}"
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def test_r_axes_is_a_proper_rotation():
    assert np.allclose(R_AXES @ R_AXES.T, np.eye(3), atol=1e-9)   # orthogonal
    assert abs(np.linalg.det(R_AXES) - 1.0) < 1e-9                 # proper (no reflection)


def test_quaternion_matrix_round_trip_on_many_rotations():
    rng = np.random.default_rng(0)
    for _ in range(200):
        v = rng.normal(size=3)
        angle = rng.uniform(0, 2 * math.pi)
        v = v / np.linalg.norm(v)
        s = math.sin(angle / 2)
        q = (v[0] * s, v[1] * s, v[2] * s, math.cos(angle / 2))
        R = quat_to_matrix(q)
        q2 = matrix_to_quaternion(R)
        R2 = quat_to_matrix(q2)
        assert np.allclose(R, R2, atol=1e-7), (q, q2)


def _check_placement_bridges_correctly(container, item, placement):
    """The core compatibility check: build the physics Object's OBB by hand (center + quat_to_matrix
    + half-extents) and confirm its world-axis-aligned extents and position match packer3d's own
    placement, independently of how the bridge internally computed the quaternion."""
    physics_placements = to_physics_placements(container, type("R", (), {"placements": [placement]})())
    pd = physics_placements[0]
    R = quat_to_matrix(pd["rotation"])
    assert np.allclose(R @ R.T, np.eye(3), atol=1e-6)
    assert abs(np.linalg.det(R) - 1.0) < 1e-6                       # proper rotation, not a reflection

    local_dims = np.array(physics_object_dims(item))
    world_extent = np.abs(R) @ local_dims                            # AABB extent of a rotated OBB
    expected_world = np.array([placement.dims[0], placement.dims[2], placement.dims[1]])  # physics axis order
    assert np.allclose(world_extent, expected_world, atol=1e-6), (world_extent, expected_world)

    L, W, H = container.dims
    cx, cy, cz = placement.center
    expected_pos = np.array([cx - L / 2.0, cz, W / 2.0 - cy])
    assert np.allclose(pd["position"], expected_pos, atol=1e-9)


def test_bridge_matches_packer_geometry_for_every_orientation_and_axis():
    # every box orientation
    for name in ("xyz", "xzy", "yxz", "yzx", "zxy", "zyx"):
        it = Item.box("b", 0.3, 0.2, 0.1, mass=1.0)
        oris = {o.name: o for o in it.orientations()}
        o = oris[name]
        d = np.asarray(o.dims)
        pos = np.array([0.5, 0.4, 0.3])
        placement = type("P", (), dict(item_id="b", shape="box", position=tuple(pos), dims=tuple(d),
                                       center=tuple(pos + d / 2), orientation=name, axis=None,
                                       radius=None, height=None, mass=1.0, fragile=False))()
        c = Container("c", (2, 2, 2))
        _check_placement_bridges_correctly(c, it, placement)
    # every cylinder axis
    for axis in ("z", "x", "y"):
        it = Item.cylinder("cy", radius=0.1, height=0.4, mass=1.0, allow_lay_down=True)
        oris = {o.axis: o for o in it.orientations()}
        o = oris[axis]
        d = np.asarray(o.dims)
        pos = np.array([0.2, 0.3, 0.1])
        placement = type("P", (), dict(item_id="cy", shape="cylinder", position=tuple(pos), dims=tuple(d),
                                       center=tuple(pos + d / 2), orientation=o.name, axis=axis,
                                       radius=0.1, height=0.4, mass=1.0, fragile=False))()
        c = Container("c", (2, 2, 2))
        _check_placement_bridges_correctly(c, it, placement)


def test_container_center_and_corners_land_on_the_bridged_obb_boundary():
    c = Container("c", (4.0, 2.0, 3.0))
    cd = physics_container_dict(c)
    assert cd["position"] == pytest.approx([0.0, 1.5, 0.0])
    assert cd["dimensions"] == pytest.approx([4.0, 3.0, 2.0])   # (L,H,W) local order
    half = np.array(cd["dimensions"]) / 2.0
    # packer3d's min corner (0,0,0) and max corner (L,W,H) must map inside +/- half_extents of the container OBB
    for corner_center in [(0.0, 0.0, 0.0), (4.0, 2.0, 3.0)]:
        cx, cy, cz = corner_center
        physics_pt = np.array([cx - 2.0, cz, 1.0 - cy])
        assert np.all(np.abs(physics_pt - np.array(cd["position"])) <= half + 1e-9)


def test_end_to_end_scenario_bridges_cleanly():
    container, items, cfg, w = load_scenario("examples/suitcase.json")
    result = pack_optimized(container, items, OptimizerConfig(time_budget_s=0, max_iterations=60, seed=0), weights=w)
    assert verify(result, items) == []
    placements = to_physics_placements(container, result)
    ids_out = {p["id"] for p in placements}
    ids_in = {p.item_id for p in result.placements}
    assert ids_out == ids_in
    items_by_id = {it.id: it for it in items}
    for p in placements:
        assert len(p["position"]) == 3 and all(math.isfinite(v) for v in p["position"])
        assert len(p["rotation"]) == 4
        n = sum(v * v for v in p["rotation"])
        assert abs(n - 1.0) < 1e-6                                   # unit quaternion
        R = quat_to_matrix(p["rotation"])
        assert abs(np.linalg.det(R) - 1.0) < 1e-6
    cd = physics_container_dict(container)
    for it in items:
        od = None
        from packer3d.physics_bridge import physics_object_dict
        od = physics_object_dict(items_by_id[it.id])
        assert od["dimensions"] == pytest.approx([it.dims[0], it.dims[2], it.dims[1]])
        assert od["mass_kg"] == it.mass
