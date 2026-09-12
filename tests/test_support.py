import math
import unittest

from physics.schema import Container, Object, Scene
from physics.support import FLOATING_MARGIN_SENTINEL_M, check_support

CONTAINER = Container(id="suitcase", dimensions=(1.0, 1.0, 1.0), position=(0, 0, 0))

SQRT2 = math.sqrt(2.0)


def make_object(id_, dims, position):
    return Object(id=id_, dimensions=dims, position=position)


def yaw(degrees):
    """Quaternion (x, y, z, w) for a rotation about world Y."""
    half = math.radians(degrees) / 2.0
    return (0.0, math.sin(half), 0.0, math.cos(half))


def pitch(degrees):
    """Quaternion (x, y, z, w) for a rotation about world X."""
    half = math.radians(degrees) / 2.0
    return (math.sin(half), 0.0, 0.0, math.cos(half))


def scene_of(*objects):
    return Scene(container=CONTAINER, objects=list(objects))


class TestSupport(unittest.TestCase):
    def test_flush_on_floor(self):
        # floor world-y = -0.5; half-height 0.1 -> bottom at -0.5 exactly.
        obj = make_object("a", (0.2, 0.2, 0.2), (0, -0.4, 0))
        (r,) = check_support(scene_of(obj))
        self.assertAlmostEqual(r.support_ratio, 1.0, places=6)
        self.assertEqual(r.supporting_objects, ["container_floor"])
        self.assertFalse(r.floating)
        self.assertFalse(r.unstable)
        self.assertGreater(r.stability_margin_m, 0)

    def test_floating_above_floor(self):
        # bottom at -0.3, gap of 0.2 m above floor (-0.5) -> no contact.
        obj = make_object("a", (0.2, 0.2, 0.2), (0, -0.2, 0))
        (r,) = check_support(scene_of(obj))
        self.assertEqual(r.support_ratio, 0.0)
        self.assertEqual(r.supporting_objects, [])
        self.assertTrue(r.floating)
        self.assertEqual(r.stability_margin_m, FLOATING_MARGIN_SENTINEL_M)
        self.assertTrue(r.unstable)

    def test_stacked_cleanly_on_other_object(self):
        base = make_object("base", (0.4, 0.2, 0.4), (0, -0.4, 0))  # top_y = -0.3
        top = make_object("top", (0.2, 0.2, 0.2), (0, -0.2, 0))  # bottom_y = -0.3
        results = {r.object_id: r for r in check_support(scene_of(base, top))}
        self.assertEqual(results["top"].supporting_objects, ["base"])
        self.assertAlmostEqual(results["top"].support_ratio, 1.0, places=6)
        self.assertFalse(results["top"].unstable)
        # base itself rests on the floor
        self.assertEqual(results["base"].supporting_objects, ["container_floor"])

    def test_precarious_small_positive_margin(self):
        # base top footprint: x in [-0.2, 0.2], z in [-0.2, 0.2], top_y = -0.3
        base = make_object("base", (0.4, 0.2, 0.4), (0, -0.4, 0))
        # perched object: bottom rect x in [0.13, 0.23], z in [-0.05, 0.05]
        perched = make_object("p", (0.1, 0.1, 0.1), (0.18, -0.25, 0))
        results = {r.object_id: r for r in check_support(scene_of(base, perched))}
        p = results["p"]
        self.assertEqual(p.supporting_objects, ["base"])
        self.assertAlmostEqual(p.support_ratio, 0.7, places=6)
        # by hand: clipped support rect x[0.13,0.20] z[-0.05,0.05], COM at (0.18, 0)
        # margin = min(0.05, 0.02, 0.05, 0.05) = 0.02
        self.assertAlmostEqual(p.stability_margin_m, 0.02, places=6)
        self.assertFalse(p.unstable)

    def test_hanging_off_edge_unstable(self):
        base = make_object("base", (0.4, 0.2, 0.4), (0, -0.4, 0))  # top rect [-0.2,0.2]^2
        # bottom rect x[0.15,0.35], z[-0.1,0.1]; only x[0.15,0.2] overlaps base
        hanger = make_object("h", (0.2, 0.1, 0.2), (0.25, -0.25, 0))
        results = {r.object_id: r for r in check_support(scene_of(base, hanger))}
        h = results["h"]
        self.assertAlmostEqual(h.support_ratio, 0.25, places=6)
        # by hand: COM (0.25, 0) vs support rect x[0.15,0.2] z[-0.1,0.1]
        # dx = 0.05, dz = 0 -> margin = -0.05
        self.assertAlmostEqual(h.stability_margin_m, -0.05, places=6)
        self.assertTrue(h.unstable)

    def test_partial_overlap_two_supports_combine(self):
        a1 = make_object("a1", (0.2, 0.2, 0.2), (-0.1, -0.4, 0))  # top rect x[-0.2,0.0]
        a2 = make_object("a2", (0.2, 0.2, 0.2), (0.1, -0.4, 0))  # top rect x[0.0,0.2]
        bridge = make_object("bridge", (0.3, 0.1, 0.2), (0, -0.25, 0))  # bottom x[-0.15,0.15]
        results = {r.object_id: r for r in check_support(scene_of(a1, a2, bridge))}
        b = results["bridge"]
        self.assertEqual(set(b.supporting_objects), {"a1", "a2"})
        self.assertAlmostEqual(b.support_ratio, 1.0, places=6)
        self.assertFalse(b.unstable)

    def test_marginal_contact_around_floating_threshold(self):
        base = make_object("base", (0.4, 0.2, 0.4), (0, -0.4, 0))  # top rect [-0.2,0.2]^2
        # footprint width/height 0.2x0.2 = area 0.04
        below = make_object("below", (0.2, 0.1, 0.2), (0.291, -0.25, 0))
        above = make_object("above", (0.2, 0.1, 0.2), (0.289, -0.25, 0))
        results = {
            r.object_id: r for r in check_support(scene_of(base, below, above))
        }
        self.assertLess(results["below"].support_ratio, 0.05)
        self.assertTrue(results["below"].floating)
        self.assertGreaterEqual(results["above"].support_ratio, 0.05)
        self.assertFalse(results["above"].floating)


class TestRotatedFootprints(unittest.TestCase):
    """Exact polygon footprints: cases the old bounding-rectangle
    approximation could not represent."""

    def test_yawed_cube_on_floor(self):
        # 0.2 cube yawed 45 deg, flat on the floor: footprint is a diamond of
        # area 0.04 (not its 0.08 bounding rectangle), fully on the floor.
        obj = Object(id="d", dimensions=(0.2, 0.2, 0.2), position=(0, -0.4, 0), rotation=yaw(45))
        (r,) = check_support(Scene(container=CONTAINER, objects=[obj]))
        self.assertAlmostEqual(r.support_ratio, 1.0, places=9)
        self.assertAlmostEqual(r.patch_areas_m2["container_floor"], 0.04, places=9)
        self.assertEqual(len(r.contact_polygon), 4)
        # A square footprint of side s has inradius s/2 at ANY yaw.
        self.assertAlmostEqual(r.stability_margin_m, 0.1, places=9)
        self.assertFalse(r.unstable)

    def test_yawed_cube_partially_on_supporter(self):
        # base top face: x,z in [-0.2, 0.2], top_y = -0.3.
        base = make_object("base", (0.4, 0.2, 0.4), (0, -0.4, 0))
        # 0.2 cube yawed 45 -> diamond, half-diagonal r = 0.1*sqrt(2), area 2r^2 = 0.04.
        # Centre it at x = 0.2 + r/2 so the base's x <= 0.2 edge keeps exactly
        # the small triangle with legs (r - r/2): area (r/2)^2 = r^2/4 = 0.005.
        # support_ratio = 0.005 / 0.04 = 0.125 exactly.
        r_half_diag = 0.1 * SQRT2
        diamond = Object(
            id="d",
            dimensions=(0.2, 0.2, 0.2),
            position=(0.2 + r_half_diag / 2.0, -0.2, 0),
            rotation=yaw(45),
        )
        results = {r.object_id: r for r in check_support(Scene(container=CONTAINER, objects=[base, diamond]))}
        d = results["d"]
        self.assertEqual(d.supporting_objects, ["base"])
        self.assertAlmostEqual(d.patch_areas_m2["base"], 0.005, places=9)
        self.assertAlmostEqual(d.support_ratio, 0.125, places=9)
        # The removed bounding-rectangle method: bottom rect 0.2828^2 = 0.08,
        # clipped to x <= 0.2 -> 0.0707 x 0.2828 = 0.02 -> ratio 0.25, i.e. it
        # over-reported support by 2x here.
        self.assertNotAlmostEqual(d.support_ratio, 0.25, places=3)
        # COM at x = 0.2 + r/2 is off the support triangle -> unstable.
        self.assertTrue(d.unstable)
        self.assertLess(d.stability_margin_m, 0.0)

    def test_pitched_box_rests_on_an_edge(self):
        # Pitched 30 deg about X, so only the bottom local edge (y=-h/2,
        # z=+d/2) touches the floor: the bottom hull is a 2-point segment.
        # Choosing d = h * tan(30) puts that edge's world z at exactly 0, i.e.
        # directly under the COM -> the COM lies ON the (degenerate) support
        # polygon, the balanced-on-an-edge boundary case.
        theta = math.radians(30)
        h, d = 0.2, 0.2 * math.tan(theta)
        drop = 0.5 * h * math.cos(theta) + 0.5 * d * math.sin(theta)
        obj = Object(
            id="p", dimensions=(0.2, h, d), position=(0, -0.5 + drop, 0), rotation=pitch(30)
        )
        (r,) = check_support(Scene(container=CONTAINER, objects=[obj]))
        self.assertEqual(r.supporting_objects, ["container_floor"])
        self.assertEqual(len(r.contact_polygon), 2)  # a segment, zero area
        self.assertAlmostEqual(r.patch_areas_m2["container_floor"], 0.0, places=12)
        # Degenerate rule: both bottom contact points are on the floor -> 1.0.
        self.assertAlmostEqual(r.support_ratio, 1.0, places=9)
        self.assertFalse(r.floating)
        # Tie-break: COM exactly on the support segment -> margin snapped to
        # 0.0 (boundary counts as supported), so not unstable.
        self.assertLessEqual(abs(r.stability_margin_m), 1e-9)
        self.assertFalse(r.unstable)

    def test_pitched_cube_com_off_contact_edge_is_unstable(self):
        # Same pitch, but a cube: its contact edge lands at world
        # z = 0.1*(cos30 - sin30) = 0.0366, so the COM (z=0) is off the
        # segment -> margin = -0.0366, unstable.
        theta = math.radians(30)
        drop = 0.1 * math.cos(theta) + 0.1 * math.sin(theta)
        obj = Object(
            id="p", dimensions=(0.2, 0.2, 0.2), position=(0, -0.5 + drop, 0), rotation=pitch(30)
        )
        (r,) = check_support(Scene(container=CONTAINER, objects=[obj]))
        self.assertAlmostEqual(r.support_ratio, 1.0, places=9)
        self.assertAlmostEqual(
            r.stability_margin_m, -0.1 * (math.cos(theta) - math.sin(theta)), places=9
        )
        self.assertTrue(r.unstable)


class TestChainAndDiagnostics(unittest.TestCase):
    def test_chain_instability_propagates_upward(self):
        a = make_object("A", (0.4, 0.2, 0.4), (0, -0.4, 0))  # floor, stable
        b = make_object("B", (0.2, 0.1, 0.2), (0.25, -0.25, 0))  # hangs off A -> unstable
        c = make_object("C", (0.2, 0.1, 0.2), (0.25, -0.15, 0))  # flush on B
        results = {r.object_id: r for r in check_support(scene_of(a, b, c))}
        self.assertFalse(results["A"].unstable)
        self.assertFalse(results["A"].supported_by_unstable)
        self.assertTrue(results["B"].unstable)
        self.assertFalse(results["B"].supported_by_unstable)  # A itself is fine
        self.assertFalse(results["C"].unstable)  # C sits squarely on B
        self.assertAlmostEqual(results["C"].support_ratio, 1.0, places=9)
        self.assertTrue(results["C"].supported_by_unstable)  # ...but B is not

    def test_three_supporters(self):
        blocks = [
            make_object(f"b{i}", (0.1, 0.1, 0.1), (x, -0.45, 0))
            for i, x in enumerate((-0.3, 0.0, 0.3))
        ]
        plank = make_object("plank", (0.8, 0.05, 0.1), (0, -0.375, 0))
        results = {r.object_id: r for r in check_support(scene_of(*blocks, plank))}
        p = results["plank"]
        self.assertEqual(p.supporting_objects, ["b0", "b1", "b2"])
        self.assertEqual(sorted(p.patch_areas_m2), ["b0", "b1", "b2"])
        for area in p.patch_areas_m2.values():
            self.assertAlmostEqual(area, 0.01, places=9)
        # 3 x 0.01 covered of a 0.8 x 0.1 bottom
        self.assertAlmostEqual(p.support_ratio, 0.03 / 0.08, places=9)
        # hull spans the outer blocks' far edges
        xs = [x for x, _ in p.contact_polygon]
        self.assertAlmostEqual(min(xs), -0.35, places=9)
        self.assertAlmostEqual(max(xs), 0.35, places=9)
        self.assertAlmostEqual(p.stability_margin_m, 0.05, places=9)  # z half-width
        self.assertFalse(p.unstable)
        self.assertFalse(p.supported_by_unstable)

    def test_deterministic(self):
        scene = scene_of(
            make_object("base", (0.4, 0.2, 0.4), (0, -0.4, 0)),
            Object(id="d", dimensions=(0.2, 0.2, 0.2), position=(0.05, -0.2, 0), rotation=yaw(37)),
            make_object("h", (0.2, 0.1, 0.2), (0.25, -0.25, 0)),
        )

        def snapshot(results):
            return [
                (
                    r.object_id,
                    r.support_ratio,
                    r.stability_margin_m,
                    tuple(r.supporting_objects),
                    r.floating,
                    r.unstable,
                    r.supported_by_unstable,
                    tuple(map(tuple, r.contact_polygon)),
                    tuple(sorted(r.patch_areas_m2.items())),
                )
                for r in results
            ]

        self.assertEqual(snapshot(check_support(scene)), snapshot(check_support(scene)))


if __name__ == "__main__":
    unittest.main()
