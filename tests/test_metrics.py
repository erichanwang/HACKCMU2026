import json
import math
import unittest

from physics.metrics import scene_metrics
from physics.schema import Container, Object, Scene
from physics.scene_geometry import precompute
from tests import fixtures


def quat(axis, angle_deg):
    ax_x, ax_y, ax_z = axis
    n = math.sqrt(ax_x * ax_x + ax_y * ax_y + ax_z * ax_z)
    ax_x, ax_y, ax_z = ax_x / n, ax_y / n, ax_z / n
    half = math.radians(angle_deg) / 2.0
    s = math.sin(half)
    return (ax_x * s, ax_y * s, ax_z * s, math.cos(half))


def make_container(dims=(2.0, 2.0, 2.0), position=(0.0, 0.0, 0.0), rotation=(0, 0, 0, 1)):
    return Container(id="suitcase", dimensions=dims, position=position, rotation=rotation)


def make_object(id_, dims, position, mass_kg=1.0, rotation=(0, 0, 0, 1)):
    return Object(id=id_, dimensions=dims, position=position, mass_kg=mass_kg, rotation=rotation)


class TestCenterOfMass(unittest.TestCase):
    def test_equal_mass_symmetric_boxes_com_at_container_center(self):
        scene = Scene(
            container=make_container(),
            objects=[
                make_object("a", (0.2, 0.2, 0.2), (-0.5, 0, 0), mass_kg=2.0),
                make_object("b", (0.2, 0.2, 0.2), (0.5, 0, 0), mass_kg=2.0),
            ],
        )
        m = scene_metrics(precompute(scene))
        for v in m["center_of_mass"]:
            self.assertAlmostEqual(v, 0.0, places=9)
        for v in m["com_offset_m"]:
            self.assertAlmostEqual(v, 0.0, places=9)

    def test_unequal_mass_shifts_com_toward_heavier_object(self):
        # COM_x = (1kg*(-1) + 3kg*(1)) / 4kg = 0.5
        scene = Scene(
            container=make_container(dims=(4.0, 4.0, 4.0)),
            objects=[
                make_object("light", (0.5, 0.5, 0.5), (-1.0, 0, 0), mass_kg=1.0),
                make_object("heavy", (0.5, 0.5, 0.5), (1.0, 0, 0), mass_kg=3.0),
            ],
        )
        m = scene_metrics(precompute(scene))
        self.assertAlmostEqual(m["center_of_mass"][0], 0.5, places=9)
        self.assertAlmostEqual(m["center_of_mass"][1], 0.0, places=9)
        self.assertAlmostEqual(m["center_of_mass"][2], 0.0, places=9)
        self.assertAlmostEqual(m["com_offset_m"][0], 0.5, places=9)

    def test_rotated_container_object_at_center_gives_zero_offset(self):
        # A rotated/offset container with a single object placed exactly at
        # the container's own position: world offset is 0 regardless of
        # rotation, so this proves com_offset_m's container-local projection
        # doesn't itself introduce a spurious offset.
        rot = quat((0, 1, 0), 37.0)
        container = make_container(dims=(2, 2, 2), position=(5.0, 1.0, -3.0), rotation=rot)
        scene = Scene(
            container=container,
            objects=[make_object("a", (0.3, 0.3, 0.3), (5.0, 1.0, -3.0), rotation=rot)],
        )
        m = scene_metrics(precompute(scene))
        for v in m["com_offset_m"]:
            self.assertAlmostEqual(v, 0.0, places=9)


class TestWallClearance(unittest.TestCase):
    def test_flush_object_has_near_zero_clearance(self):
        scene = fixtures.scene_object_touching_wall()
        m = scene_metrics(precompute(scene))
        # laptop is flush against the -x wall (and also rests on the floor).
        self.assertAlmostEqual(m["per_object"]["laptop"]["wall_clearance_m"], 0.0, places=6)

    def test_interior_object_has_positive_clearance(self):
        # charger (from valid_packed_scene, stacked on headphones_case, away
        # from every wall): half-extents=(0.045,0.015,0.03),
        # center=(0.10,0.085,-0.10) in a container with half-extents
        # (0.28,0.115,0.18). Tightest axis is z: |center_z| + half_z =
        # 0.10+0.03=0.13, clearance = 0.18-0.13 = 0.05 (x gives 0.135, y
        # gives 0.07, both larger).
        scene = fixtures.valid_packed_scene()
        m = scene_metrics(precompute(scene))
        self.assertAlmostEqual(m["per_object"]["charger"]["wall_clearance_m"], 0.05, places=6)

    def test_penetrating_object_has_negative_clearance(self):
        scene = Scene(
            container=make_container(),  # half-extents (1,1,1)
            objects=[make_object("a", (1.0, 1.0, 1.0), (0.51, 0, 0))],  # pokes 0.01 past +x
        )
        m = scene_metrics(precompute(scene))
        self.assertLess(m["per_object"]["a"]["wall_clearance_m"], 0.0)
        self.assertAlmostEqual(m["per_object"]["a"]["wall_clearance_m"], -0.01, places=6)


class TestNearestNeighborGap(unittest.TestCase):
    def test_known_gap_along_x(self):
        # box A: x in [-0.5, 0.5]. box B center_x=1.02, half=0.5 -> x in
        # [0.52, 1.52]. gap = 0.52 - 0.5 = 0.02. y/z fully overlap (both
        # centered at 0 with the same half-extents), so those axes are
        # negative (overlap) and don't win the max.
        scene = Scene(
            container=make_container(dims=(10.0, 10.0, 10.0)),
            objects=[
                make_object("a", (1.0, 1.0, 1.0), (0.0, 0.0, 0.0)),
                make_object("b", (1.0, 1.0, 1.0), (1.02, 0.0, 0.0)),
            ],
        )
        m = scene_metrics(precompute(scene))
        self.assertAlmostEqual(m["per_object"]["a"]["nearest_neighbor_gap_m"], 0.02, places=6)
        self.assertAlmostEqual(m["per_object"]["b"]["nearest_neighbor_gap_m"], 0.02, places=6)
        self.assertEqual(m["per_object"]["a"]["nearest_neighbor_id"], "b")
        self.assertEqual(m["per_object"]["b"]["nearest_neighbor_id"], "a")

    def test_overlapping_boxes_gap_is_zero(self):
        scene = Scene(
            container=make_container(dims=(10.0, 10.0, 10.0)),
            objects=[
                make_object("a", (1.0, 1.0, 1.0), (0.0, 0.0, 0.0)),
                make_object("b", (1.0, 1.0, 1.0), (0.3, 0.0, 0.0)),
            ],
        )
        m = scene_metrics(precompute(scene))
        self.assertEqual(m["per_object"]["a"]["nearest_neighbor_gap_m"], 0.0)
        self.assertEqual(m["per_object"]["b"]["nearest_neighbor_gap_m"], 0.0)

    def test_single_object_has_no_neighbor(self):
        scene = Scene(
            container=make_container(),
            objects=[make_object("a", (0.2, 0.2, 0.2), (0.0, 0.0, 0.0))],
        )
        m = scene_metrics(precompute(scene))
        self.assertIsNone(m["per_object"]["a"]["nearest_neighbor_gap_m"])
        self.assertIsNone(m["per_object"]["a"]["nearest_neighbor_id"])


class TestFillRatio(unittest.TestCase):
    def test_small_box_in_unit_container(self):
        scene = Scene(
            container=make_container(dims=(1.0, 1.0, 1.0)),
            objects=[make_object("a", (0.1, 0.1, 0.1), (0.0, 0.0, 0.0))],
        )
        m = scene_metrics(precompute(scene))
        self.assertAlmostEqual(m["fill_ratio"], 0.001, places=9)


class TestJsonSerializable(unittest.TestCase):
    def test_all_values_json_dumpsable(self):
        for scene_fn in (
            fixtures.valid_packed_scene,
            fixtures.scene_object_touching_wall,
            fixtures.scene_rotated_object_in_corner,
        ):
            m = scene_metrics(precompute(scene_fn()))
            json.dumps(m)  # must not raise

    def test_single_and_zero_object_scenes_json_dumpsable(self):
        scene = Scene(container=make_container(), objects=[])
        json.dumps(scene_metrics(precompute(scene)))
        scene = Scene(
            container=make_container(),
            objects=[make_object("a", (0.2, 0.2, 0.2), (0, 0, 0))],
        )
        json.dumps(scene_metrics(precompute(scene)))


if __name__ == "__main__":
    unittest.main()
