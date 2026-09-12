"""`physics.metrics.plan_quality` -- carry/tip/fragile/void metrics.

Every case is a hand-computable scene: a 2x2x2 m container centred at the
origin (so container-local coordinates ARE world coordinates and the base
plane is y = -1) unless the test is specifically about a rotated container.
"""
from __future__ import annotations

import json
import math
import unittest

from physics.metrics import plan_quality, scene_metrics
from physics.scene_geometry import container_up_axis, precompute
from physics.schema import Constraints, Container, Object, Scene

CUBE = Container(id="box", dimensions=(2.0, 2.0, 2.0), position=(0.0, 0.0, 0.0))


def q(*objects: Object, container: Container = CUBE) -> dict:
    return plan_quality(precompute(Scene(container=container, objects=list(objects))))


def box(oid, position, dimensions=(0.2, 0.2, 0.2), mass_kg=1.0, **kw) -> Object:
    return Object(id=oid, dimensions=dimensions, position=position, mass_kg=mass_kg, **kw)


class FrameTest(unittest.TestCase):
    def test_axis_aligned_container_is_y_up_z_long(self):
        geom = precompute(Scene(container=Container(id="c", dimensions=(0.34, 0.20, 0.50)), objects=[]))
        self.assertEqual(container_up_axis(geom), (1, 1.0))
        m = plan_quality(geom)
        self.assertEqual((m["up_axis"], m["long_axis"]), ("y", "z"))

    def test_container_rolled_90_degrees_moves_the_up_axis(self):
        # +90 deg about Z: the container's local x axis now points along world +Y.
        s = math.sqrt(0.5)
        rolled = Container(id="c", dimensions=(0.34, 0.20, 0.50), rotation=(0.0, 0.0, s, s))
        geom = precompute(Scene(container=rolled, objects=[]))
        self.assertEqual(container_up_axis(geom), (0, 1.0))
        self.assertEqual(plan_quality(geom)["up_axis"], "x")


class CenterOfMassTest(unittest.TestCase):
    def test_height_fraction_floor_versus_lid(self):
        self.assertAlmostEqual(q(box("a", (0.0, -0.9, 0.0)))["com_height_fraction"], 0.05)
        self.assertAlmostEqual(q(box("a", (0.0, 0.9, 0.0)))["com_height_fraction"], 0.95)

    def test_height_fraction_is_mass_weighted(self):
        # 3 kg on the floor, 1 kg in the lid -> CoM at (3*0.05 + 1*0.95) / 4.
        m = q(box("floor", (0.0, -0.9, 0.0), mass_kg=3.0), box("lid", (0.0, 0.9, 0.0), mass_kg=1.0))
        self.assertAlmostEqual(m["com_height_fraction"], 0.275)

    def test_lateral_offset_is_horizontal_distance_from_the_base_centre(self):
        m = q(box("a", (0.3, -0.9, 0.4)))
        self.assertAlmostEqual(m["com_lateral_offset_m"], 0.5)  # 3-4-5

    def test_long_axis_fraction_runs_along_the_longest_horizontal_axis(self):
        # 1 x 1 x 4 m container: z is the wheels <-> handle axis, half-extent 2.
        long_bag = Container(id="bag", dimensions=(1.0, 1.0, 4.0), position=(0.0, 0.0, 0.0))
        m = q(box("a", (0.0, -0.4, 0.8)), container=long_bag)
        self.assertEqual(m["long_axis"], "z")
        self.assertAlmostEqual(m["com_long_axis_fraction"], 0.7)  # (0.8 + 2) / 4

    def test_centred_load_sits_mid_way_along_the_long_axis(self):
        self.assertAlmostEqual(q(box("a", (0.0, -0.9, 0.0)))["com_long_axis_fraction"], 0.5)


class TipOverTest(unittest.TestCase):
    def test_centred_load_is_symmetric(self):
        margins = q(box("a", (0.0, -0.9, 0.0)))["tip_over_margin_deg"]
        self.assertEqual(sorted(margins), ["+x", "+z", "-x", "-z"])
        self.assertEqual(len(set(round(v, 9) for v in margins.values())), 1)

    def test_margin_is_atan_distance_over_height(self):
        m = q(box("a", (0.5, -0.9, 0.0)))  # CoM 0.1 m above the base, 0.5 m from the +x edge
        self.assertAlmostEqual(m["tip_over_margin_deg"]["+x"], math.degrees(math.atan2(0.5, 0.1)))
        self.assertAlmostEqual(m["tip_over_margin_deg"]["-x"], math.degrees(math.atan2(1.5, 0.1)))
        self.assertAlmostEqual(m["min_tip_over_margin_deg"], m["tip_over_margin_deg"]["+x"])

    def test_high_mass_is_less_stable_than_low_mass(self):
        low = q(box("a", (0.5, -0.9, 0.0)))["min_tip_over_margin_deg"]
        high = q(box("a", (0.5, 0.9, 0.0)))["min_tip_over_margin_deg"]
        self.assertLess(high, low)
        self.assertGreater(low, 0.0)

    def test_com_outside_the_base_gives_zero_margin(self):
        m = q(box("a", (1.5, -0.9, 0.0)))  # hanging past the +x wall
        self.assertEqual(m["tip_over_margin_deg"]["+x"], 0.0)
        self.assertEqual(m["min_tip_over_margin_deg"], 0.0)

    def test_margins_stay_within_zero_and_ninety(self):
        for m in (q(box("a", (0.0, -0.999, 0.0))), q(box("a", (0.9, 0.9, 0.9))), q()):
            for v in m["tip_over_margin_deg"].values():
                self.assertGreaterEqual(v, 0.0)
                self.assertLessEqual(v, 90.0)


class MassAboveFragileTest(unittest.TestCase):
    CAM = box("cam", (0.0, -0.9, 0.0), dimensions=(0.4, 0.2, 0.4),
              constraints=Constraints(fragile=True))

    def test_no_fragile_items_no_entries(self):
        self.assertEqual(q(box("a", (0.0, -0.9, 0.0)))["mass_above_fragile_kg"], {})

    def test_weight_directly_on_top_counts_in_full(self):
        m = q(self.CAM, box("brick", (0.0, -0.6, 0.0), dimensions=(0.4, 0.2, 0.4), mass_kg=2.0))
        self.assertAlmostEqual(m["mass_above_fragile_kg"]["cam"], 2.0)

    def test_weight_beside_it_counts_for_nothing(self):
        m = q(self.CAM, box("brick", (0.9, -0.6, 0.0), dimensions=(0.4, 0.2, 0.4), mass_kg=2.0))
        self.assertAlmostEqual(m["mass_above_fragile_kg"]["cam"], 0.0)

    def test_half_overlapping_weight_counts_by_column_share(self):
        # brick offset by half its length: half of its footprint is over the camera.
        m = q(self.CAM, box("brick", (0.2, -0.6, 0.0), dimensions=(0.4, 0.2, 0.4), mass_kg=2.0))
        self.assertAlmostEqual(m["mass_above_fragile_kg"]["cam"], 1.0)

    def test_weight_two_layers_up_still_presses_down(self):
        m = q(self.CAM,
              box("mid", (0.0, -0.6, 0.0), dimensions=(0.4, 0.2, 0.4), mass_kg=1.0),
              box("top", (0.0, -0.3, 0.0), dimensions=(0.4, 0.2, 0.4), mass_kg=2.0))
        self.assertAlmostEqual(m["mass_above_fragile_kg"]["cam"], 3.0)

    def test_cannot_support_weight_is_treated_as_fragile(self):
        laptop = box("laptop", (0.0, -0.9, 0.0), dimensions=(0.4, 0.2, 0.4),
                     constraints=Constraints(cannot_support_weight=True))
        m = q(laptop, box("brick", (0.0, -0.6, 0.0), dimensions=(0.4, 0.2, 0.4), mass_kg=2.0))
        self.assertAlmostEqual(m["mass_above_fragile_kg"]["laptop"], 2.0)


class VoidTest(unittest.TestCase):
    def test_one_solid_block_is_perfectly_compact(self):
        m = q(box("a", (0.0, 0.0, 0.0), dimensions=(1.0, 1.0, 1.0)))
        self.assertAlmostEqual(m["compactness"], 1.0)
        self.assertAlmostEqual(m["enclosed_void_m3"], 0.0)
        self.assertAlmostEqual(m["packed_envelope_m3"], 1.0)
        self.assertAlmostEqual(m["free_headroom_m3"], 7.0)  # 8 m^3 container - 1 m^3 envelope

    def test_two_spread_blocks_trap_void_inside_the_envelope(self):
        m = q(box("a", (-0.5, 0.0, 0.0), dimensions=(1.0, 1.0, 1.0)),
              box("b", (0.5, 0.0, 0.0), dimensions=(1.0, 1.0, 1.0)))
        self.assertAlmostEqual(m["packed_envelope_m3"], 2.0)
        self.assertAlmostEqual(m["compactness"], 1.0)  # they touch: no gap between them
        m2 = q(box("a", (-0.9, 0.0, 0.0), dimensions=(0.2, 1.0, 1.0)),
               box("b", (0.9, 0.0, 0.0), dimensions=(0.2, 1.0, 1.0)))
        self.assertAlmostEqual(m2["packed_envelope_m3"], 2.0)  # spans the full 2 m of x
        self.assertAlmostEqual(m2["enclosed_void_m3"], 1.6)  # 2.0 - 2 * 0.2
        self.assertAlmostEqual(m2["compactness"], 0.2)

    def test_headroom_is_the_space_outside_the_packed_block(self):
        # everything in the bottom half: 4 m^3 of clear space above it
        m = q(box("a", (0.0, -0.5, 0.0), dimensions=(2.0, 1.0, 2.0)))
        self.assertAlmostEqual(m["free_headroom_m3"], 4.0)
        self.assertAlmostEqual(m["compactness"], 1.0)


class ContractTest(unittest.TestCase):
    def test_empty_scene_is_the_empty_container(self):
        m = q()
        self.assertAlmostEqual(m["com_height_fraction"], 0.5)  # falls back to the container centre
        self.assertEqual(m["packed_envelope_m3"], 0.0)
        self.assertEqual(m["compactness"], 0.0)
        self.assertEqual(m["mass_above_fragile_kg"], {})

    def test_massless_scene_does_not_divide_by_zero(self):
        m = q(box("obstacle", (0.5, -0.9, 0.0), mass_kg=0.0))
        self.assertAlmostEqual(m["com_height_fraction"], 0.5)
        self.assertAlmostEqual(m["com_lateral_offset_m"], 0.0)

    def test_output_is_plain_json(self):
        m = q(box("a", (0.3, -0.5, 0.2), constraints=Constraints(fragile=True)),
              box("b", (0.3, -0.1, 0.2), mass_kg=2.0))
        self.assertEqual(json.loads(json.dumps(m)), m)  # no numpy scalars

    def test_scene_metrics_keys_are_unchanged(self):
        """plan_quality is additive: the existing payload keeps its exact shape."""
        geom = precompute(Scene(container=CUBE, objects=[box("a", (0.0, -0.9, 0.0))]))
        m = scene_metrics(geom)
        self.assertEqual(sorted(m), ["center_of_mass", "com_offset_m", "fill_ratio",
                                     "per_object", "total_mass_kg"])
        self.assertEqual(sorted(m["per_object"]["a"]),
                         ["footprint_area_m2", "height_above_floor_m", "nearest_neighbor_gap_m",
                          "nearest_neighbor_id", "volume_m3", "wall_clearance_m"])


if __name__ == "__main__":
    unittest.main()
