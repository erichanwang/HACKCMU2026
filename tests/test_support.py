import unittest

from physics.schema import Container, Object, Scene
from physics.support import FLOATING_MARGIN_SENTINEL_M, check_support

CONTAINER = Container(id="suitcase", dimensions=(1.0, 1.0, 1.0), position=(0, 0, 0))


def make_object(id_, dims, position):
    return Object(id=id_, dimensions=dims, position=position)


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


if __name__ == "__main__":
    unittest.main()
