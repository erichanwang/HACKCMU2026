"""Support is a whole-body property: verify() must judge it the way the decoder does.

The decoder tests support once, over an item's whole bounding-box footprint. verify() used to
test it per heightmap sub-box with that sub-box's own area as the base, so one overhanging
1/8 patch of a scanned item read as "support ratio 0.000" for a placement the decoder had just
accepted (seen on tools/packbench/fixtures/carryon_overfilled.json, dopp_kit_scanned).
"""
import unittest

from packer3d import Container, Item, PackResult, verify
from packer3d.models import Placement

MIN_SUPPORT = 0.7
CONTAINER = Container(id="bin", dims=(0.30, 0.10, 0.30), shape="box",
                      gravity=True, min_support=MIN_SUPPORT)


def _scanned() -> Item:
    # 20cm x 10cm x 10cm, full 4x2 heightmap -> eight 5cm x 5cm sub-boxes, four of them in
    # the x = 0.15..0.20 column that hangs off the slab below.
    data = {"id": "kit", "width": 20.0, "depth": 10.0, "height": 10.0, "cellSize": 5.0,
            "heights": [[10, 10], [10, 10], [10, 10], [10, 10]]}
    return Item.from_scanned_heightmap(data, mass=1.0)


def _placed(item: Item, pos) -> Placement:
    d = item.dims
    return Placement(item_id=item.id, shape=item.shape, position=tuple(pos), dims=d,
                     center=tuple(pos[k] + d[k] / 2.0 for k in range(3)), orientation="xyz",
                     axis=None, radius=item.radius, height=item.height, mass=item.mass,
                     fragile=item.fragile, volume=item.volume, scan_shape=item.scan_shape)


class SupportIsWholeBodyTest(unittest.TestCase):
    def setUp(self):
        self.slab = Item.box("slab", length=0.15, width=0.10, height=0.10, mass=2.0)
        self.kit = _scanned()
        self.result = PackResult(strategy="hand", container=CONTAINER, metrics={}, unpacked=[],
                                 placements=[_placed(self.slab, (0.0, 0.0, 0.0)),
                                             _placed(self.kit, (0.0, 0.0, 0.10))])

    def test_the_overhang_this_test_depends_on_really_exists(self):
        boxes = self.kit.solid_boxes()
        self.assertEqual(len(boxes), 8)
        # the last column of sub-boxes starts at x=0.15, i.e. past the slab's far edge
        self.assertAlmostEqual(max(lo[0] for lo, _ in boxes), 0.15)

    def test_a_partly_overhanging_scanned_item_is_supported(self):
        # 0.15 of the 0.20 footprint is carried -> 0.75 >= min_support, the decoder's reading.
        self.assertEqual(verify(self.result, [self.slab, self.kit]), [])

    def test_a_genuinely_unsupported_body_is_still_reported(self):
        # slide the kit out until only 0.10 of 0.20 rests on the slab: 0.5 < 0.7.
        self.result.placements[1] = _placed(self.kit, (0.05, 0.0, 0.10))
        errors = verify(self.result, [self.slab, self.kit])
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("kit is not supported", errors[0])
        self.assertIn("support ratio 0.500", errors[0])

    def test_support_is_reported_once_per_item_not_once_per_sub_box(self):
        # floating in mid-air: eight sub-boxes, still one message.
        self.result.placements[1] = _placed(self.kit, (0.0, 0.0, 0.20))
        errors = verify(self.result, [self.slab, self.kit])
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("support ratio 0.000", errors[0])


if __name__ == "__main__":
    unittest.main()
