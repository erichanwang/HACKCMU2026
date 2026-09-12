"""An upright cylinder in a round container only has to clear the bore.

``Item.fits_in`` learned this in 9996fdb; the decoder and verify still applied the rectangular
bbox-corner test, so a r=0.4 can was either rejected from a R=0.5 tube or (once fits_in let it
through) placed by the decoder and then flagged by verify. A cylinder laid on its side really
does sweep an h x 2r rectangle and must keep the corner test.
"""
import math
import unittest

from packer3d import Container, Item, PackResult, pack_naive, verify
from packer3d.models import Placement

TUBE = Container(id="tube", dims=(1.0, 1.0, 1.0), shape="cylinder", gravity=True)


def _placed(item: Item, pos, orientation="cyl_axis_z", axis="z", dims=None) -> Placement:
    d = dims or item.dims
    return Placement(item_id=item.id, shape=item.shape, position=tuple(pos), dims=d,
                     center=tuple(pos[k] + d[k] / 2.0 for k in range(3)), orientation=orientation,
                     axis=axis, radius=item.radius, height=item.height, mass=item.mass,
                     fragile=item.fragile, volume=item.volume)


def _result(*placements) -> PackResult:
    return PackResult(strategy="hand", container=TUBE, placements=list(placements),
                      unpacked=[], metrics={})


class CylinderContainmentTest(unittest.TestCase):
    def test_a_fat_can_packs_into_the_bore_and_verifies(self):
        # r = 0.4 <= R = 0.5, but hypot(0.8, 0.8) = 1.13 > 1.0: the rectangular test rejected it.
        can = Item.cylinder("can", radius=0.4, height=0.5, mass=1.0, keep_upright=True)
        result = pack_naive(TUBE, [can])
        self.assertEqual(result.unpacked, [])
        self.assertEqual(verify(result, [can]), [])
        p = result.placements[0]
        # wherever it landed, its axis is within R - r = 0.1 of the container's
        self.assertLessEqual(math.hypot(p.center[0] - 0.5, p.center[1] - 0.5), 0.1 + 1e-9)

    def test_verify_accepts_the_centred_can_the_corner_test_rejected(self):
        can = Item.cylinder("can", radius=0.4, height=0.5, mass=1.0, keep_upright=True)
        self.assertEqual(verify(_result(_placed(can, (0.1, 0.1, 0.0))), [can]), [])

    def test_a_can_shoved_against_the_curved_wall_is_still_rejected(self):
        can = Item.cylinder("can", radius=0.4, height=0.5, mass=1.0, keep_upright=True)
        # axis 0.141 off centre with only 0.1 of slack -- the far side of the can is outside R
        errors = verify(_result(_placed(can, (0.2, 0.2, 0.0))), [can])
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("reaches past the wall", errors[0])

    def test_a_rod_laid_on_its_side_keeps_the_rectangular_test(self):
        # bbox 0.95 x 0.4 x 0.4: its footprint really is a rectangle, hypot(0.95, 0.4) > 1.0.
        rod = Item.cylinder("rod", radius=0.2, height=0.95, mass=1.0)
        laid = _placed(rod, (0.025, 0.3, 0.0), orientation="cyl_axis_x", axis="x",
                       dims=(0.95, 0.4, 0.4))
        errors = verify(_result(laid), [rod])
        self.assertTrue(any("outside the cylinder wall" in e for e in errors), errors)

    def test_the_solver_never_hands_out_a_placement_verify_rejects(self):
        items = [Item.cylinder("can", 0.4, 0.3, 1.0, keep_upright=True),
                 Item.cylinder("jar", 0.3, 0.3, 0.8, keep_upright=True),
                 Item.box("brick", 0.2, 0.2, 0.2, 0.5)]
        result = pack_naive(TUBE, items)
        self.assertEqual(verify(result, items), [])


if __name__ == "__main__":
    unittest.main()
