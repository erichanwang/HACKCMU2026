"""``balance_masses`` may only swap items whose *real* shape is interchangeable.

The swap groups used to be keyed on the oriented bounding box plus the fragile flag, on the
theory that two items with the same box are interchangeable.  That was true when every item
was a solid box; it is not true for a scanned item, which carries a ``height_grid`` and
decomposes into cavity solids.  A bowl and a solid block of the same outer size shared a
group, so balancing could exchange them -- moving a cavity to where there is none, dropping
the cup the decoder had nested in the bowl's interior inside a solid block, and leaving the
cup's ``nested_in`` pointing at a host that has moved across the container.

Before the fix, ``test_bowl_never_swaps_with_a_solid_block_of_the_same_size`` reported one
swap and ``verify`` went from clean to four errors (collision, two stale-nesting errors, and
the cup left unsupported).  ``test_two_plain_boxes_still_swap`` is the control: the very same
packing with the bowl's grid stripped is two plain boxes, and that swap still happens, so the
fix narrows the group key rather than switching balancing off.
"""
import unittest
from dataclasses import replace

from packer3d import Container, Item, balance_masses, pack_naive, verify

# The bowl is heavy and packs on the left, the block is light and packs on the right, so moving
# the mass right is a big centre-of-mass win and the greedy takes the swap if the group allows it.
_BOWL_MASS, _BLOCK_MASS = 2.0, 0.4


def _bowl() -> Item:
    """20 x 20 x 10 cm bowl: a 10cm-thick rim around a 10 x 10 cm interior 1cm off the floor."""
    grid = [
        [10, 10, 10, 10],
        [10, 1, 1, 10],
        [10, 1, 1, 10],
        [10, 10, 10, 10],
    ]
    data = {"id": "bowl", "width": 20.0, "depth": 20.0, "height": 10.0, "cellSize": 5.0, "heights": grid}
    # fragile=False so the bowl shares the block's fragile flag -- the point is that the bounding
    # boxes and the flags match and only the real shape differs.
    return Item.from_scanned_heightmap(data, mass=_BOWL_MASS, fragile=False)


def _block() -> Item:
    """Solid box with exactly the bowl's bounding box: same group under the old key."""
    return Item.box("block", 0.20, 0.20, 0.10, mass=_BLOCK_MASS, keep_upright=True)


def _cup() -> Item:
    return Item.box("cup", 0.08, 0.08, 0.06, mass=0.2)


def _container() -> Container:
    # bowl + block fill the floor along x, with floor left over for the cup when it cannot nest.
    # Not symmetric: the two slots (x centres 0.1 and 0.3) sit either side of the container's
    # centre of mass target (0.235), so moving the heavy item right is a real improvement.
    return Container(id="box", dims=(0.47, 0.20, 0.101), shape="box", gravity=True)


class BalanceShapeSwapTest(unittest.TestCase):
    def test_bowl_and_block_share_a_bounding_box_and_a_fragile_flag(self):
        bowl, block = _bowl(), _block()
        self.assertEqual(bowl.dims, block.dims)
        self.assertEqual(bowl.fragile, block.fragile)
        self.assertIsNotNone(bowl.height_grid)          # ... but only one of them has a cavity
        self.assertLess(bowl.occupied_volume, block.occupied_volume)

    def test_bowl_never_swaps_with_a_solid_block_of_the_same_size(self):
        bowl, block, cup = _bowl(), _block(), _cup()
        items = [bowl, block, cup]
        result = pack_naive(_container(), items)
        cup_p = next(p for p in result.placements if p.item_id == "cup")
        self.assertEqual(cup_p.nested_in["item_id"], "bowl")   # the nest the swap used to break
        self.assertEqual(verify(result, items), [])

        self.assertEqual(balance_masses(_container(), result.placements), 0)
        self.assertEqual(verify(result, items), [])

    def test_two_plain_boxes_still_swap(self):
        # identical scenario with the bowl's scan stripped: two plain boxes, so the swap is real
        # and must still happen. This is what the pre-fix code did to the scanned bowl above.
        bowl = replace(_bowl(), height_grid=None, grid_cell=None, scan_shape=None, true_volume=None)
        block, cup = _block(), _cup()
        items = [bowl, block, cup]
        result = pack_naive(_container(), items)
        before = {p.item_id: tuple(p.position) for p in result.placements}

        self.assertEqual(balance_masses(_container(), result.placements), 1)
        after = {p.item_id: tuple(p.position) for p in result.placements}
        self.assertEqual(after["bowl"], before["block"])    # the two boxes traded places
        self.assertEqual(after["block"], before["bowl"])
        self.assertEqual(after["cup"], before["cup"])
        self.assertEqual(verify(result, items), [])


if __name__ == "__main__":
    unittest.main()
