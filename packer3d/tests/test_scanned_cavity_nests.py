"""A scan that arrives the way the phone sends it -- no `fragile` key anywhere -- must nest.

This is the regression guard for FIXES.md section 4 ("cavity nesting does not fire in a real
solve"). Two defaults collided: a shape with a real cavity has volume/bbox < 0.9, so
`from_scanned_heightmap` classified it "irregular", which defaulted `fragile=True`; and the
decoder's fragile-below rule refuses to rest anything on ANY solid of a fragile item -- the floor
of its own cavity included. The one place resting is intended was the one place it was forbidden,
so no real document could ever produce a nest. `tests/test_nesting.py` missed it because it hands
its bowl `fragile=False` by hand; this file never passes a flag.

An item somebody really called fragile still protects every surface -- see
`test_verify_invariants.py::test_nesting_into_a_fragile_scanned_item_is_still_rejected`.
"""
import math
import unittest

from packer3d import Container, Item, pack_naive, verify
from packer3d.scenario import load_scenario


def bowl_document(w=0.22, h=0.08, d=0.22, rim=0.03, floor=0.01) -> dict:
    """An open box seen from above, in the server document form (SCAN_OUTPUT.md): a full-height
    rim around a low interior. Same shape as `tests/plan_contract/make_plan.py::bowl_scan`."""
    cell = 0.01
    rows, cols = math.ceil(w / cell), math.ceil(d / cell)
    r = math.ceil(rim / cell)
    heights = [[h if (i < r or j < r or i >= rows - r or j >= cols - r) else floor
                for j in range(cols)] for i in range(rows)]
    return {"id": "bowl", "dimensions": [w, h, d], "cellSize": cell, "heights": heights,
            "rigidity": "rigid", "mass": 0.5}


class ScannedCavityNestsTests(unittest.TestCase):
    SCENARIO = {"container": {"id": "bag", "dims": [0.24, 0.24, 0.10]},
                "items": [bowl_document(),
                          {"id": "socks", "shape": "box", "dims": [0.1, 0.1, 0.04], "mass": 0.1}]}

    def test_a_scanned_open_box_is_not_fragile_by_default(self):
        bowl = Item.from_scanned_heightmap(bowl_document())
        self.assertEqual(bowl.scan_shape, "irregular")
        self.assertIsNotNone(bowl.height_grid)
        self.assertFalse(bowl.fragile, "a height grid says where the surfaces are; a blanket "
                                       "'nothing may rest on it' also blocks its own cavity")
        self.assertTrue(bowl.keep_upright, "the other irregular default still stands")

    def test_the_socks_nest_in_the_cavity(self):
        container, items, _cfg, _w = load_scenario(self.SCENARIO)
        result = pack_naive(container, items)
        self.assertEqual({p.item_id for p in result.placements}, {"bowl", "socks"},
                         result.unpacked)
        self.assertEqual(verify(result, items), [])
        socks = next(p for p in result.placements if p.item_id == "socks")
        bowl = next(p for p in result.placements if p.item_id == "bowl")
        self.assertLess(socks.position[2], 0.05, "near the cavity floor, not on the rim")
        self.assertIsNotNone(socks.nested_in, "the decoder must declare what it did")
        self.assertEqual(socks.nested_in["item_id"], "bowl")
        self.assertLessEqual(socks.position[2] + socks.dims[2],
                             bowl.position[2] + bowl.dims[2] + 1e-6)

    def test_a_declared_fragile_container_still_refuses_the_nest(self):
        scenario = {"container": self.SCENARIO["container"],
                    "items": [dict(bowl_document(), rigidity="fragile", fragile=True),
                              self.SCENARIO["items"][1]]}
        container, items, _cfg, _w = load_scenario(scenario)
        result = pack_naive(container, items)
        self.assertEqual({p.item_id for p in result.placements}, {"bowl"})
        self.assertEqual([u["id"] for u in result.unpacked], ["socks"])


if __name__ == "__main__":
    unittest.main()
