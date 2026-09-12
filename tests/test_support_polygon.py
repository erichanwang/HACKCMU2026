"""Support-polygon (COM-in-hull) tests, and the bridging case the hull alone
cannot see.

Frame check first: physics is X=right, Y=UP, Z=forward (physics.schema), so
"the support polygon" is a hull in XZ and the COM projection is (center.x,
center.z). Every scene below puts the container floor at Y = -0.5.
"""
import math
import unittest

from physics.schema import Container, Object, Scene
from physics.support import FLOATING_MARGIN_SENTINEL_M, check_support

# 2 m along X so two separated supporters fit; floor plane at Y = -0.5.
CONTAINER = Container(id="suitcase", dimensions=(2.0, 1.0, 1.0), position=(0, 0, 0))
FLOOR_Y = -0.5


def obj(id_, dims, position, rotation=(0.0, 0.0, 0.0, 1.0)):
    return Object(id=id_, dimensions=dims, position=position, rotation=rotation)


def on_floor(id_, dims, x=0.0, z=0.0):
    """Box resting flat on the container floor."""
    return obj(id_, dims, (x, FLOOR_Y + dims[1] / 2.0, z))


def on_top_of(id_, dims, base, x=0.0, z=0.0):
    """Box resting flat on `base`'s top face."""
    top_y = base.position[1] + base.dimensions[1] / 2.0
    return obj(id_, dims, (x, top_y + dims[1] / 2.0, z))


def results_for(*objects):
    return {r.object_id: r for r in check_support(Scene(container=CONTAINER, objects=list(objects)))}


class TestSupportPolygon(unittest.TestCase):
    """COM projection vs the convex hull of the contact patches."""

    def test_com_inside_hull_is_stable_with_positive_margin(self):
        base = on_floor("base", (0.4, 0.2, 0.4))
        top = on_top_of("top", (0.2, 0.2, 0.2), base)
        r = results_for(base, top)["top"]
        # Hull is the 0.2 x 0.2 bottom face; COM is dead centre.
        self.assertAlmostEqual(r.stability_margin_m, 0.1, places=9)
        self.assertFalse(r.unstable)
        self.assertTrue(r.com_over_patch)

    def test_com_outside_hull_is_unstable_with_negative_margin(self):
        # Contact is all on one side of the object: the patch stops at x=0.20
        # and the COM is at x=0.32, so the COM hangs past the hull edge.
        base = on_floor("base", (0.4, 0.2, 0.4))
        top = on_top_of("top", (0.4, 0.2, 0.4), base, x=0.32)
        r = results_for(base, top)["top"]
        # patch x in [0.12, 0.20] (0.08 of 0.40 wide) -> ratio 0.2, COM at 0.32.
        self.assertAlmostEqual(r.stability_margin_m, -0.12, places=9)
        self.assertTrue(r.unstable)
        self.assertFalse(r.com_over_patch)

    def test_area_fraction_and_polygon_answer_different_questions(self):
        # support_ratio 0.75 (three quarters of the base is on the supporter)
        # yet the COM still projects inside -> supported AND balanced.
        base = on_floor("base", (0.4, 0.2, 0.4))
        top = on_top_of("top", (0.4, 0.2, 0.4), base, x=0.1)
        r = results_for(base, top)["top"]
        self.assertAlmostEqual(r.support_ratio, 0.75, places=9)
        self.assertGreater(r.stability_margin_m, 0.0)
        self.assertFalse(r.unstable)

    def test_margin_is_signed_distance_to_the_nearest_hull_edge(self):
        # Rectangular hull 0.4 (x) by 0.2 (z): the nearest edge is a z edge.
        base = on_floor("base", (0.4, 0.2, 0.2))
        top = on_top_of("top", (0.4, 0.2, 0.4), base)
        r = results_for(base, top)["top"]
        self.assertAlmostEqual(r.stability_margin_m, 0.1, places=9)

    def test_yawed_slab_margin_uses_the_rotated_hull(self):
        # 45-degree yaw: the hull is a diamond in XZ, not the AABB. Its
        # corners are at +-0.1*sqrt(2) but its edges are still 0.1 from the COM,
        # which is what the margin must report (the AABB would say 0.1*sqrt(2)).
        r = results_for(obj("a", (0.2, 0.2, 0.2), (0, FLOOR_Y + 0.1, 0),
                            (0.0, math.sin(math.pi / 8), 0.0, math.cos(math.pi / 8))))["a"]
        self.assertAlmostEqual(r.stability_margin_m, 0.1, places=9)
        self.assertTrue(r.com_over_patch)
        self.assertEqual(len(r.contact_polygon), 4)
        xs = [x for x, _ in r.contact_polygon]
        self.assertAlmostEqual(max(xs), 0.1 * math.sqrt(2.0), places=9)

    def test_unsupported_object_reports_the_sentinel_not_a_distance(self):
        r = results_for(obj("a", (0.2, 0.2, 0.2), (0, 0.2, 0)))["a"]
        self.assertEqual(r.stability_margin_m, FLOATING_MARGIN_SENTINEL_M)
        self.assertEqual(r.contact_polygon, [])
        self.assertFalse(r.com_over_patch)
        self.assertTrue(r.floating)


class TestBridging(unittest.TestCase):
    """A slab across two separated supporters.

    The convex hull of the two contact patches spans the gap between them, so
    the COM projects INSIDE it and the static criterion (correctly, for rigid
    bodies -- a plank on two bricks does not topple) reports stable. What the
    hull cannot say is that the COM has no contact patch under it; that is
    `com_over_patch`.
    """

    def _bridge(self, slab_x):
        # Two 0.2-wide feet at x = -0.4 and x = +0.4, top face at Y = -0.3;
        # a 1.0-long slab across both, so the gap under it is x in (-0.3, 0.3).
        left = on_floor("left", (0.2, 0.2, 0.4), x=-0.4)
        right = on_floor("right", (0.2, 0.2, 0.4), x=0.4)
        slab = obj("slab", (1.0, 0.05, 0.3), (slab_x, -0.3 + 0.025, 0.0))
        return results_for(left, right, slab)["slab"]

    def test_com_over_the_gap_is_flagged_by_com_over_patch(self):
        r = self._bridge(0.0)
        self.assertEqual(sorted(r.supporting_objects), ["left", "right"])
        # Two patches, 0.2 x 0.3 each, out of a 1.0 x 0.3 base.
        self.assertAlmostEqual(r.support_ratio, 0.4, places=9)
        # The hull spans the void, so the static criterion says stable...
        self.assertGreater(r.stability_margin_m, 0.0)
        self.assertFalse(r.unstable)
        # ...but nothing is actually under the COM.
        self.assertFalse(r.com_over_patch)

    def test_com_over_one_supporter_stays_stable(self):
        # Same two feet, slab shifted so its COM sits over the left foot.
        r = self._bridge(-0.4)
        self.assertGreater(r.stability_margin_m, 0.0)
        self.assertFalse(r.unstable)
        self.assertTrue(r.com_over_patch)

    def test_hull_spans_the_gap_between_both_patches(self):
        xs = sorted(x for x, _ in self._bridge(0.0).contact_polygon)
        self.assertAlmostEqual(xs[0], -0.5, places=9)
        self.assertAlmostEqual(xs[-1], 0.5, places=9)

    def test_patch_areas_are_reported_per_supporter(self):
        r = self._bridge(0.0)
        self.assertAlmostEqual(r.patch_areas_m2["left"], 0.06, places=9)
        self.assertAlmostEqual(r.patch_areas_m2["right"], 0.06, places=9)

    def test_slab_off_both_feet_is_unstable_and_off_patch(self):
        # Slab pushed right until its COM clears the right foot entirely.
        r = self._bridge(0.95)
        self.assertLess(r.stability_margin_m, 0.0)
        self.assertTrue(r.unstable)
        self.assertFalse(r.com_over_patch)


class TestComOverPatchEdges(unittest.TestCase):
    def test_com_on_a_patch_boundary_counts_as_over_it(self):
        # Slab's COM exactly above the left foot's inner edge (x = -0.3).
        left = on_floor("left", (0.2, 0.2, 0.4), x=-0.4)
        right = on_floor("right", (0.2, 0.2, 0.4), x=0.4)
        slab = obj("slab", (1.0, 0.05, 0.3), (-0.3, -0.275, 0.0))
        r = results_for(left, right, slab)["slab"]
        self.assertTrue(r.com_over_patch)

    def test_edge_balanced_box_is_over_its_degenerate_patch(self):
        # 45-degree pitch about X: the box touches the floor on one edge, and
        # the COM projects onto that segment. Boundary counts as supported.
        s = math.sin(math.pi / 8)
        c = math.cos(math.pi / 8)
        half_diag = 0.1 * math.sqrt(2.0)
        r = results_for(obj("a", (0.2, 0.2, 0.2), (0, FLOOR_Y + half_diag, 0), (s, 0.0, 0.0, c)))["a"]
        self.assertEqual(len(r.contact_polygon), 2)  # a segment
        self.assertLessEqual(abs(r.stability_margin_m), 1e-9)
        self.assertFalse(r.unstable)
        self.assertTrue(r.com_over_patch)

    def test_object_wholly_on_the_floor_is_over_its_patch(self):
        r = results_for(on_floor("a", (0.2, 0.2, 0.2)))["a"]
        self.assertEqual(r.supporting_objects, ["container_floor"])
        self.assertTrue(r.com_over_patch)

    def test_three_feet_with_the_com_in_the_middle_gap(self):
        # Feet at x = -0.6, -0.2, +0.6 under a slab spanning [-0.7, 0.7]: the
        # COM at x = 0 sits in the gap between the middle and right feet, but
        # well inside the hull. Same signature as the two-foot bridge.
        feet = [on_floor(f"f{i}", (0.2, 0.2, 0.4), x=x) for i, x in enumerate((-0.6, -0.2, 0.6))]
        slab = obj("slab", (1.4, 0.05, 0.3), (0.0, -0.275, 0.0))
        r = results_for(*feet, slab)["slab"]
        self.assertEqual(len(r.supporting_objects), 3)
        self.assertGreater(r.stability_margin_m, 0.0)
        self.assertFalse(r.com_over_patch)


if __name__ == "__main__":
    unittest.main()
