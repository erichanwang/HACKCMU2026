import math
import unittest

import numpy as np

from physics.containment import (
    ContainmentResult,
    check_containment,
    check_no_duplicate_ids,
    check_scene_containment,
)
from physics.geometry import obb_from
from physics.schema import Constraints, Container, Object, Scene
from physics.scene_geometry import precompute
from tests import fixtures


def quat(axis, angle_deg):
    ax = np.asarray(axis, dtype=float)
    ax = ax / np.linalg.norm(ax)
    half = math.radians(angle_deg) / 2.0
    s = math.sin(half)
    x, y, z = ax * s
    return (x, y, z, math.cos(half))


def make_container(dims=(2.0, 2.0, 2.0), position=(0.0, 0.0, 0.0), rotation=(0, 0, 0, 1)):
    return Container(id="suitcase", dimensions=dims, position=position, rotation=rotation)


def make_object(id_, dims, position, rotation=(0, 0, 0, 1)):
    return Object(id=id_, dimensions=dims, position=position, rotation=rotation)


class TestCheckContainment(unittest.TestCase):
    def test_fully_contained_axis_aligned(self):
        container = obb_from(make_container())
        obj = obb_from(make_object("a", (0.5, 0.5, 0.5), (0, 0, 0)))
        result = check_containment(container, obj)
        self.assertTrue(result.contained)
        self.assertEqual(result.penetrating_vertices, [])
        self.assertEqual(result.penetration_depth_m, 0.0)
        self.assertEqual(result.violated_walls, [])

    def test_fully_contained_rotated_object(self):
        container = obb_from(make_container())
        obj = obb_from(make_object("a", (0.5, 0.5, 0.5), (0, 0, 0), quat((0, 1, 0), 45)))
        result = check_containment(container, obj)
        self.assertTrue(result.contained)

    def test_object_exactly_touching_wall(self):
        container = obb_from(make_container())  # half-extents (1,1,1)
        obj = obb_from(make_object("a", (1.0, 1.0, 1.0), (0.5, 0, 0)))  # vertex at x=1.0
        result = check_containment(container, obj)
        self.assertTrue(result.contained)
        self.assertEqual(result.violated_walls, [])

    def test_object_penetrating_wall_known_amount(self):
        container = obb_from(make_container())
        obj = obb_from(make_object("a", (1.0, 1.0, 1.0), (0.51, 0, 0)))  # vertex at x=1.01
        result = check_containment(container, obj)
        self.assertFalse(result.contained)
        self.assertAlmostEqual(result.penetration_depth_m, 0.01, places=6)
        self.assertEqual(result.violated_walls, ["+x"])
        self.assertEqual(len(result.penetrating_vertices), 4)  # the 4 vertices on the +x face

    def test_center_inside_corner_pokes_out_due_to_rotation(self):
        # Center is well inside the container (not touching any wall), but a
        # 45-degree roll pushes one corner through the +x wall. A naive
        # "is the center inside the box" check would wrongly pass this.
        container = obb_from(make_container())  # half-extents (1,1,1)
        obj = obb_from(
            make_object("a", (0.4, 0.4, 0.4), (0.75, 0, 0), quat((0, 0, 1), 45))
        )
        result = check_containment(container, obj)
        self.assertFalse(result.contained)
        self.assertIn("+x", result.violated_walls)
        expected_depth = 0.75 + 0.2 * math.cos(math.radians(45)) + 0.2 * math.sin(
            math.radians(45)
        ) - 1.0
        self.assertAlmostEqual(result.penetration_depth_m, expected_depth, places=6)
        # Single wall involved -- per_wall_depth_m must report exactly it,
        # at the same depth as the overall max.
        self.assertEqual(set(result.per_wall_depth_m), {"+x"})
        self.assertAlmostEqual(result.per_wall_depth_m["+x"], expected_depth, places=6)

    def test_object_entirely_outside(self):
        container = obb_from(make_container())
        obj = obb_from(make_object("a", (0.5, 0.5, 0.5), (10.0, 0, 0)))
        result = check_containment(container, obj)
        self.assertFalse(result.contained)
        self.assertEqual(len(result.penetrating_vertices), 8)

    def test_object_bigger_than_container_in_one_dimension(self):
        container = obb_from(make_container())  # half-extents (1,1,1)
        obj = obb_from(make_object("a", (3.0, 0.5, 0.5), (0, 0, 0)))  # half-extent x=1.5
        result = check_containment(container, obj)
        self.assertFalse(result.contained)
        self.assertIn("+x", result.violated_walls)
        self.assertIn("-x", result.violated_walls)
        self.assertAlmostEqual(result.penetration_depth_m, 0.5, places=6)

    def test_rotated_container_with_fitting_object(self):
        # Container itself rotated 45 degrees about Y and offset from origin.
        rot = quat((0, 1, 0), 45)
        container_entity = make_container(dims=(2, 2, 2), position=(5, 1, -3), rotation=rot)
        container = obb_from(container_entity)
        # Object shares the container's rotation and is centered inside it,
        # so in the container's local frame it's just a small centered box.
        obj = obb_from(make_object("a", (0.5, 0.5, 0.5), (5, 1, -3), rot))
        result = check_containment(container, obj)
        self.assertTrue(result.contained)


class TestObbFromValidation(unittest.TestCase):
    def test_nan_dimensions_raises(self):
        scene = Scene(
            container=make_container(),
            objects=[make_object("bad", (float("nan"), 1, 1), (0, 0, 0))],
        )
        with self.assertRaises(ValueError) as ctx:
            check_scene_containment(scene)
        self.assertIn("bad", str(ctx.exception))

    def test_nan_position_raises(self):
        scene = Scene(
            container=make_container(),
            objects=[make_object("bad", (1, 1, 1), (float("nan"), 0, 0))],
        )
        with self.assertRaises(ValueError) as ctx:
            check_scene_containment(scene)
        self.assertIn("bad", str(ctx.exception))


class TestDuplicateIds(unittest.TestCase):
    def test_duplicate_ids_raise(self):
        scene = Scene(
            container=make_container(),
            objects=[
                make_object("dup", (0.1, 0.1, 0.1), (0, 0, 0)),
                make_object("dup", (0.1, 0.1, 0.1), (0.2, 0, 0)),
            ],
        )
        with self.assertRaises(ValueError) as ctx:
            check_no_duplicate_ids(scene)
        self.assertIn("dup", str(ctx.exception))

    def test_unique_ids_ok(self):
        scene = Scene(
            container=make_container(),
            objects=[
                make_object("a", (0.1, 0.1, 0.1), (0, 0, 0)),
                make_object("b", (0.1, 0.1, 0.1), (0.2, 0, 0)),
            ],
        )
        check_no_duplicate_ids(scene)  # should not raise


class TestCheckSceneContainment(unittest.TestCase):
    def test_returns_only_violations(self):
        scene = Scene(
            container=make_container(),
            objects=[
                make_object("fits", (0.5, 0.5, 0.5), (0, 0, 0)),
                make_object("too_big", (3.0, 0.5, 0.5), (0, 0, 0)),
            ],
        )
        results = check_scene_containment(scene)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].object_id, "too_big")
        self.assertFalse(results[0].contained)

    def test_all_fit_returns_empty_list(self):
        scene = Scene(
            container=make_container(),
            objects=[make_object("fits", (0.5, 0.5, 0.5), (0, 0, 0))],
        )
        results = check_scene_containment(scene)
        self.assertEqual(results, [])

    def test_zero_objects_returns_empty_list(self):
        scene = Scene(container=make_container(), objects=[])
        self.assertEqual(check_scene_containment(scene), [])

    def test_precomputed_geom_matches_default(self):
        # scene_with_wall_penetration: laptop violates -X by 0.03m, shoe is
        # fully contained -- so exactly one violation, with a known depth.
        scene = fixtures.scene_with_wall_penetration()
        geom = precompute(scene)
        direct = check_scene_containment(scene)
        via_geom = check_scene_containment(scene, geom=geom)
        self.assertEqual(len(direct), len(via_geom))
        for a, b in zip(direct, via_geom):
            self.assertEqual(a.object_id, b.object_id)
            self.assertEqual(a.contained, b.contained)
            self.assertAlmostEqual(a.penetration_depth_m, b.penetration_depth_m, places=9)
            self.assertEqual(a.violated_walls, b.violated_walls)
            self.assertEqual(set(a.per_wall_depth_m), set(b.per_wall_depth_m))
            for wall, depth in a.per_wall_depth_m.items():
                self.assertAlmostEqual(depth, b.per_wall_depth_m[wall], places=9)
            np.testing.assert_allclose(
                np.array(a.penetrating_vertices), np.array(b.penetrating_vertices)
            )


class TestPerWallDepth(unittest.TestCase):
    def test_two_walls_violated_by_different_known_amounts(self):
        # Container half-extents (1,1,1). Object half-extents (0.5,0.5,0.5)
        # at center (0.53, 0, -0.51):
        #   x-range = [0.03, 1.03]  -> +x overshoot = 1.03 - 1 = 0.03
        #   y-range = [-0.5, 0.5]   -> fully inside
        #   z-range = [-1.01, -0.01] -> -z overshoot = |-1.01| - 1 = 0.01
        container = obb_from(make_container())
        obj = obb_from(make_object("a", (1.0, 1.0, 1.0), (0.53, 0.0, -0.51)))
        result = check_containment(container, obj)
        self.assertFalse(result.contained)
        self.assertEqual(result.violated_walls, ["+x", "-z"])
        self.assertAlmostEqual(result.per_wall_depth_m["+x"], 0.03, places=6)
        self.assertAlmostEqual(result.per_wall_depth_m["-z"], 0.01, places=6)
        self.assertEqual(set(result.per_wall_depth_m), {"+x", "-z"})
        self.assertAlmostEqual(result.penetration_depth_m, 0.03, places=6)
        self.assertAlmostEqual(
            result.penetration_depth_m, max(result.per_wall_depth_m.values()), places=9
        )


if __name__ == "__main__":
    unittest.main()
