"""The decoder records WHICH cavity it nested an item into, on the placement it commits.

Consumers that model an item as one bounding box (verify, the physics adapter, the Swift plan
loader, the AR overlay) cannot otherwise tell a legal cup-in-bowl nest from a collision.
Serialised shape, additive to to_dict()/to_json():

    "nested_in": {"item_id": "bowl", "cavity": [x, y, z, dx, dy, dz]}

min corner + extents in packer3d's own frame, like position/dims. The key is OMITTED for an
ordinary placement (Placement.nested_in is None there).
"""
import unittest

from packer3d import Container, Item, pack_naive, verify


def _bowl() -> Item:
    # 20 x 20 x 10 cm: a 5cm rim all round a 10 x 10 cm interior only 1cm deep.
    grid = [[10, 10, 10, 10],
            [10, 1, 1, 10],
            [10, 1, 1, 10],
            [10, 10, 10, 10]]
    data = {"id": "bowl", "width": 20.0, "depth": 20.0, "height": 10.0, "cellSize": 5.0,
            "heights": grid}
    return Item.from_scanned_heightmap(data, mass=0.4, fragile=False)


class NestedInTest(unittest.TestCase):
    def test_a_nested_placement_names_the_host_and_the_cavity_it_went_into(self):
        bowl, cup = _bowl(), Item.box("cup", 0.08, 0.08, 0.06, mass=0.2)
        container = Container(id="box", dims=(0.20, 0.20, 0.101), gravity=True)
        result = pack_naive(container, [bowl, cup])
        self.assertEqual(verify(result, [bowl, cup]), [])

        by_id = {p.item_id: p for p in result.placements}
        self.assertIsNone(by_id["bowl"].nested_in)
        nest = by_id["cup"].nested_in
        self.assertIsNotNone(nest, "the cup nested in the bowl but said nothing")
        self.assertEqual(nest["item_id"], "bowl")

        x, y, z, dx, dy, dz = nest["cavity"]
        blo = by_id["bowl"].position
        # cavity floor = the bowl's 1cm interior, lid = its 10cm rim; never the whole bbox
        self.assertAlmostEqual(z, blo[2] + 0.01, places=6)
        self.assertAlmostEqual(dz, 0.09, places=6)
        # footprint is the cup's own, clipped to the host -- 8cm, not the bowl's 20cm
        self.assertAlmostEqual(dx, 0.08, places=6)
        self.assertAlmostEqual(dy, 0.08, places=6)
        # and it is inside the bowl's 10cm interior, not over the rim
        self.assertGreaterEqual(x, blo[0] + 0.05 - 1e-9)
        self.assertGreaterEqual(y, blo[1] + 0.05 - 1e-9)

    def test_the_cavity_survives_json_round_trip_and_is_omitted_otherwise(self):
        bowl, cup = _bowl(), Item.box("cup", 0.08, 0.08, 0.06, mass=0.2)
        container = Container(id="box", dims=(0.20, 0.20, 0.101), gravity=True)
        result = pack_naive(container, [bowl, cup])
        placements = {p["item_id"]: p for p in result.to_dict()["placements"]}
        self.assertNotIn("nested_in", placements["bowl"])
        self.assertEqual(placements["cup"]["nested_in"]["item_id"], "bowl")
        # Wire form: `position` + `dims`, the spelling `server/app_plan.py::_nesting` reads.
        self.assertEqual(len(placements["cup"]["nested_in"]["position"]), 3)
        self.assertEqual(len(placements["cup"]["nested_in"]["dims"]), 3)
        import json
        self.assertIn("nested_in", json.loads(result.to_json())["placements"][1])

    def test_items_side_by_side_report_no_nesting(self):
        a = Item.box("a", 0.10, 0.10, 0.10, mass=1.0)
        b = Item.box("b", 0.10, 0.10, 0.10, mass=1.0)
        container = Container(id="box", dims=(0.20, 0.10, 0.10), gravity=True)
        result = pack_naive(container, [a, b])
        self.assertEqual(len(result.placements), 2)
        for p in result.placements:
            self.assertIsNone(p.nested_in)
            self.assertNotIn("nested_in", p.to_dict())

    def test_verify_rejects_an_invented_nest(self):
        # a lie downstream would act on: two boxes that merely touch, one claiming a cavity.
        a = Item.box("a", 0.10, 0.10, 0.10, mass=1.0)
        b = Item.box("b", 0.10, 0.10, 0.10, mass=1.0)
        container = Container(id="box", dims=(0.20, 0.10, 0.10), gravity=True)
        result = pack_naive(container, [a, b])
        result.placements[1].nested_in = {"item_id": "a", "cavity": [0.0, 0.0, 0.0, 0.1, 0.1, 0.1]}
        errors = verify(result, [a, b])
        self.assertTrue(any("stale or invented nesting" in e for e in errors), errors)
        self.assertTrue(any("not a cavity" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
