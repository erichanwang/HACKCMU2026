"""Proves the scanned heightmap actually changes a packing decision: a smaller item can
nest inside a scanned item's real cavity (a bowl's interior), not just its bounding box.

Before the fix, every item -- including one built from a heightmap -- is added to the
decoder as a single solid bounding box, so a bowl "eats" its entire footprint from the
floor to its rim height. A cup can then only rest on TOP of the bowl's rim, needing
bowl_height + cup_height of headroom. This test uses a container just tall enough for the
bowl alone plus a hair of clearance -- not enough for cup-on-rim -- so the cup can only be
packed at all if the solver actually uses the bowl's cavity.
"""
import unittest

from packer3d import Container, Item, pack_naive, verify


def _bowl() -> Item:
    # 20cm x 20cm x 10cm bowl: a 10cm rim all around a 10cm x 10cm x 1cm-deep interior.
    cm = lambda v: v / 100.0
    grid = [
        [10, 10, 10, 10],
        [10, 1, 1, 10],
        [10, 1, 1, 10],
        [10, 10, 10, 10],
    ]
    data = {"id": "bowl", "width": 20.0, "depth": 20.0, "height": 10.0, "cellSize": 5.0, "heights": grid}
    # fragile=False: a bowl is a legitimate nesting target, not an unstable surface to avoid
    # stacking on (the default fragile=True for an "irregular" scan is about the latter).
    return Item.from_scanned_heightmap(data, mass=0.4, fragile=False)


def _cup() -> Item:
    return Item.box("cup", length=0.08, width=0.08, height=0.06, mass=0.2)


class NestingTest(unittest.TestCase):
    def test_bowl_scan_produces_a_cavity(self):
        bowl = _bowl()
        self.assertEqual(bowl.scan_shape, "irregular")
        self.assertIsNotNone(bowl.height_grid)
        boxes = bowl.solid_boxes()
        # the four interior cells (height 1cm) must NOT be full-height like the twelve rim
        # cells (10cm) -- that's the whole cavity this test depends on.
        heights = sorted({round(hi[2], 6) for _, hi in boxes})
        self.assertEqual(heights, [0.01, 0.10])

    def test_cup_nests_inside_bowl_cavity(self):
        bowl, cup = _bowl(), _cup()
        # exactly the bowl's own footprint, and only tall enough for the bowl plus a hair of
        # clearance -- cup-on-rim (0.10 + 0.06 = 0.16m) cannot possibly fit.
        container = Container(id="box", dims=(0.20, 0.20, 0.101), shape="box", gravity=True)

        result = pack_naive(container, [bowl, cup])
        self.assertEqual(errors := verify(result, [bowl, cup]), [])

        packed_ids = {p.item_id for p in result.placements}
        self.assertEqual(packed_ids, {"bowl", "cup"}, result.unpacked)

        cup_p = next(p for p in result.placements if p.item_id == "cup")
        bowl_p = next(p for p in result.placements if p.item_id == "bowl")
        # the win: cup sits near the cavity floor (0.01m), nowhere close to the rim (0.10m).
        self.assertLess(cup_p.position[2], 0.05)
        self.assertLessEqual(cup_p.position[2] + cup_p.dims[2], bowl_p.position[2] + bowl_p.dims[2] + 1e-6)

    def test_without_nesting_the_cup_does_not_fit(self):
        # same scenario, but with the bowl's height_grid stripped -- i.e. exactly the old
        # bounding-box-only behaviour this fix replaces. This is the "before" measurement.
        from dataclasses import replace
        bowl = replace(_bowl(), height_grid=None, scan_shape=None)
        cup = _cup()
        container = Container(id="box", dims=(0.20, 0.20, 0.101), shape="box", gravity=True)

        result = pack_naive(container, [bowl, cup])
        packed_ids = {p.item_id for p in result.placements}
        self.assertEqual(packed_ids, {"bowl"})
        self.assertEqual(result.unpacked[0]["id"], "cup")


if __name__ == "__main__":
    unittest.main()
