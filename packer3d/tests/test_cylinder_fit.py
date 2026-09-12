"""Item.fits_in: an upright cylinder only has to clear a round container's bore."""
from packer3d import Container, Item
from packer3d.bounds import lower_bounds

BORE = Container("bore", (1.0, 1.0, 1.0), shape="cylinder")   # R = 0.5


def test_upright_cylinder_fits_the_bore_not_its_bounding_square():
    # r = 0.4 <= R = 0.5 drops straight in; the bbox diagonal hypot(0.8, 0.8) = 1.13 does not.
    can = Item.cylinder("can", 0.4, 0.5, keep_upright=True)
    assert can.fits_in(BORE)
    assert lower_bounds(BORE, [can])["unfit_items"] == []


def test_a_cylinder_too_wide_or_too_tall_for_the_bore_still_does_not_fit():
    assert not Item.cylinder("drum", 0.55, 0.5, keep_upright=True).fits_in(BORE)
    assert not Item.cylinder("mast", 0.1, 1.5, keep_upright=True).fits_in(BORE)


def test_boxes_and_laid_cylinders_keep_the_diagonal_test():
    assert Item.box("plate", 0.6, 0.6, 0.2).fits_in(BORE)
    # upright-only, so it cannot stand on edge: its corners hit the wall, hypot(0.8, 0.8) > 1.0
    assert not Item.box("slab", 0.8, 0.8, 0.2, keep_upright=True).fits_in(BORE)
    # laid down, a cylinder sweeps a length x diameter rectangle, which does need the diagonal:
    # hypot(0.95, 0.4) = 1.03 > 1.0, and upright it is too tall for this shallow bore.
    flat = Container("flat", (1.0, 1.0, 0.45), shape="cylinder")
    assert not Item.cylinder("rod", 0.2, 0.95).fits_in(flat)
    assert Item.cylinder("stub", 0.2, 0.4).fits_in(flat)
