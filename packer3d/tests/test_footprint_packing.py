"""The scanned footprint (SCAN_OUTPUT.md) actually changes a packing decision: an item may sit
in the part of another item's bounding box that its real convex cross-section does not fill.

The geometry in `FootprintAgreementTests` below is deliberately the SAME numbers as
`tests/test_packer3d_adapter.py::FootprintAgreementTests` (the physics gate's half of this
feature, commit 1aa1b24): an L-shaped scan whose hull is a pentagon cutting the missing quadrant
off along a diagonal, with a small box sitting in what is left of that quadrant. Both halves have
to accept exactly the same placement -- if the solver honours a footprint the gate does not, the
gate rejects placements the solver was right to make; if it honours fewer, we lose packing.
"""
import unittest

import numpy as np

from packer3d import Container, Item, pack_naive, verify
from packer3d.decoder import PackState
from packer3d.models import (convex_clip_2d, footprints_overlap, oriented_footprint,
                             polygon_area_2d, rect_polygon)
from packer3d.scenario import load_scenario

# An L-shape missing its top-right quadrant, as the phone sends it (0.2 x 0.2 item, [x, z] pairs
# about the box centre). Hulled, this is the pentagon that cuts the corner off along the
# (0.1, 0) - (0, 0.1) diagonal -- the closest convex approximation of the missing quadrant.
L_FOOTPRINT = [[-0.1, -0.1], [0.1, -0.1], [0.1, 0.0], [0.0, 0.0], [0.0, 0.1], [-0.1, 0.1]]


def _lshape(**over) -> Item:
    return Item.box("lshape", 0.2, 0.2, 0.1, mass=0.4, footprint=L_FOOTPRINT, **over)


class FootprintValidationTests(unittest.TestCase):
    def test_the_polygon_is_hulled_and_ccw(self):
        it = _lshape()
        self.assertEqual(it.footprint,
                         ((-0.1, -0.1), (0.1, -0.1), (0.1, 0.0), (0.0, 0.1), (-0.1, 0.1)))
        self.assertAlmostEqual(polygon_area_2d(it.footprint), 0.04 - 0.005, places=12)
        # the prism is the volume that counts, not the bounding box
        self.assertAlmostEqual(it.volume, 0.035 * 0.1, places=12)
        self.assertAlmostEqual(it.occupied_volume, 0.035 * 0.1, places=12)

    def test_a_footprint_outside_the_bounding_box_is_rejected(self):
        with self.assertRaises(ValueError):
            Item.box("bad", 0.2, 0.2, 0.1, footprint=[[-0.1, -0.1], [0.3, -0.1], [0.0, 0.1]])

    def test_a_degenerate_footprint_is_rejected(self):
        with self.assertRaises(ValueError):
            Item.box("bad", 0.2, 0.2, 0.1, footprint=[[-0.1, -0.1], [0.0, 0.0], [0.1, 0.1]])

    def test_only_a_box_carries_one(self):
        with self.assertRaises(ValueError):
            Item("c", "cylinder", (0.2, 0.2, 0.1), radius=0.1, height=0.1, footprint=L_FOOTPRINT)

    def test_a_scan_document_carries_it_through_the_loader(self):
        # both spellings a scenario can use: a plain box, and the heightmap/server document form
        _c, items, _cfg, _w = load_scenario({
            "container": {"dims": [0.4, 0.4, 0.2]},
            "items": [{"id": "plain", "shape": "box", "dims": [0.2, 0.2, 0.1],
                       "footprint": L_FOOTPRINT},
                      {"id": "scanned", "dimensions": [0.2, 0.1, 0.2], "cellSize": 0.1,
                       "heights": [[0.1, 0.1], [0.1, 0.1]], "footprint": L_FOOTPRINT}]})
        self.assertEqual(len(items[0].footprint), 5)
        self.assertEqual(items[1].footprint, items[0].footprint)


class FootprintOverlapTests(unittest.TestCase):
    def test_a_touching_pair_is_not_an_overlap(self):
        a = ((0.0, 0.0), (0.1, 0.0), (0.1, 0.1), (0.0, 0.1))
        b = ((0.1, 0.0), (0.2, 0.0), (0.2, 0.1), (0.1, 0.1))
        self.assertFalse(footprints_overlap(a, b))
        self.assertEqual(polygon_area_2d(convex_clip_2d(a, b)), 0.0)

    def test_a_real_overlap_is_one(self):
        a = ((0.0, 0.0), (0.1, 0.0), (0.1, 0.1), (0.0, 0.1))
        b = ((0.05, 0.05), (0.15, 0.05), (0.15, 0.15), (0.05, 0.15))
        self.assertTrue(footprints_overlap(a, b))

    def test_a_rectangle_polygon_reproduces_the_aabb_verdict(self):
        r = rect_polygon((0.0, 0.0, 0.0), (0.1, 0.1, 0.1))
        self.assertEqual(r, ((0.0, 0.0), (0.1, 0.0), (0.1, 0.1), (0.0, 0.1)))


class FootprintOrientationTests(unittest.TestCase):
    """Same gate and the same signs as `physics.packer3d_adapter._FOOTPRINT_XZ`: item-local
    (fx, fz) -> packer world (fx, fz) under "xyz" and (fz, fx) under "yxz"; every other
    orientation lays the item on its side and falls back to the bounding box."""

    def test_xyz_offsets_the_polygon_by_the_box_centre(self):
        got = oriented_footprint(_lshape(), (1.0, 2.0, 0.0), (0.2, 0.2, 0.1), "xyz")
        for (gx, gy), (fx, fz) in zip(got, _lshape().footprint):
            self.assertAlmostEqual(gx, 1.1 + fx, places=12)
            self.assertAlmostEqual(gy, 2.1 + fz, places=12)

    def test_yxz_swaps_x_and_y_and_stays_ccw(self):
        item = _lshape()
        got = oriented_footprint(item, (0.0, 0.0, 0.0), (0.2, 0.2, 0.1), "yxz")
        self.assertEqual(set(got), {(0.1 + fz, 0.1 + fx) for fx, fz in item.footprint})
        # CCW: the shoelace sum of the swapped polygon must still be positive
        xs = [p[0] for p in got]
        ys = [p[1] for p in got]
        signed = sum(xs[i] * ys[(i + 1) % len(got)] - xs[(i + 1) % len(got)] * ys[i]
                     for i in range(len(got))) / 2.0
        self.assertGreater(signed, 0.0)

    def test_a_tipped_orientation_falls_back_to_the_box(self):
        for name in ("xzy", "yzx", "zxy", "zyx"):
            self.assertIsNone(oriented_footprint(_lshape(), (0, 0, 0), (0.2, 0.1, 0.2), name))


class FootprintAgreementTests(unittest.TestCase):
    """The solver-side twin of `tests/test_packer3d_adapter.py::FootprintAgreementTests`."""

    CONTAINER = Container(id="box", dims=(0.4, 0.4, 0.2), shape="box", gravity=True)
    PEBBLE = Item.box("pebble", 0.04, 0.04, 0.05, mass=0.05)

    def _decoded(self, lshape):
        """The L at the origin, then the pebble forced into the missing quadrant."""
        st = PackState(self.CONTAINER)
        self.assertTrue(st.place(lshape))
        return st

    def _pebble_fits_at(self, st, corner=(0.16, 0.16, 0.0)) -> bool:
        ok, _score = st._feasible_and_score(
            np.array([corner]), np.asarray(self.PEBBLE.dims, dtype=float), self.PEBBLE.mass,
            self.PEBBLE.bbox_volume, np.zeros(1), self.PEBBLE.fragile, False, None)
        return bool(ok[0])

    def test_the_pebble_in_the_missing_corner_is_accepted(self):
        self.assertTrue(self._pebble_fits_at(self._decoded(_lshape())),
                        "the pebble only overlaps space the hull does not fill")

    def test_without_the_footprint_the_same_spot_collides(self):
        st = self._decoded(Item.box("lshape", 0.2, 0.2, 0.1, mass=0.4))
        self.assertFalse(self._pebble_fits_at(st),
                         "without a footprint the L is its whole bounding box")

    def test_verify_accepts_the_placement_the_decoder_made(self):
        """The load-bearing half: verify() re-derives collisions independently, so it must
        decompose the footprint the same way or every correct placement reads as an overlap."""
        lshape = _lshape()
        st = self._decoded(lshape)
        st.place(self.PEBBLE)
        from packer3d.objective import build_result
        result = build_result("test", self.CONTAINER, st.placements, st.unpacked,
                              [lshape, self.PEBBLE])
        placed = {p.item_id: p for p in result.placements}
        self.assertIn("pebble", placed, result.unpacked)
        self.assertEqual(verify(result, [lshape, self.PEBBLE]), [])
        # and no nest is declared: a footprint miss is not a height-grid cavity
        self.assertIsNone(placed["pebble"].nested_in)


class LBracketNotchTests(unittest.TestCase):
    """The packbench fixture `tools/packbench/fixtures/l_bracket_notch.json`, inline: a crate the
    bracket fills wall to wall, so the notch box fits nowhere except the wedge its hull leaves."""

    SCENARIO = {
        "container": {"id": "crate", "dims": [0.40, 0.40, 0.12]},
        "items": [
            {"id": "l_bracket", "shape": "box", "dims": [0.40, 0.40, 0.12], "mass": 1.2,
             "keep_upright": True,
             "footprint": [[-0.20, -0.20], [0.20, -0.20], [0.20, 0.00], [0.00, 0.20], [-0.20, 0.20]]},
            {"id": "notch_box", "shape": "box", "dims": [0.08, 0.08, 0.12], "mass": 0.3,
             "keep_upright": True},
        ],
    }

    def test_the_notch_box_packs_with_the_footprint(self):
        container, items, _cfg, _w = load_scenario(self.SCENARIO)
        result = pack_naive(container, items)
        self.assertEqual({p.item_id for p in result.placements}, {"l_bracket", "notch_box"},
                         result.unpacked)
        self.assertEqual(verify(result, items), [])
        notch = next(p for p in result.placements if p.item_id == "notch_box")
        # inside the bracket's bounding box, past the hull's diagonal (x + y >= 0.6)
        self.assertGreaterEqual(notch.position[0] + notch.position[1], 0.6 - 1e-9)
        self.assertLess(notch.position[0], 0.4)

    def test_without_the_footprint_it_does_not_fit(self):
        """The before measurement: the same scenario with the footprint dropped."""
        scenario = {"container": self.SCENARIO["container"],
                    "items": [{k: v for k, v in it.items() if k != "footprint"}
                              for it in self.SCENARIO["items"]]}
        container, items, _cfg, _w = load_scenario(scenario)
        result = pack_naive(container, items)
        self.assertEqual({p.item_id for p in result.placements}, {"l_bracket"})
        self.assertEqual([u["id"] for u in result.unpacked], ["notch_box"])


if __name__ == "__main__":
    unittest.main()
