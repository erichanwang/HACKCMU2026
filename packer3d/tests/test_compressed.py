"""Soft items pack at height / k (Item.compressed), fed from the server's scan document."""
from packer3d import Container, Item, load_scenario, pack_naive, verify

# SCAN_OUTPUT.md form: metres, dimensions = [width, height, depth], heights rows x cols = 3 x 2.
SHIRT = {"id": "shirt", "dimensions": [0.3, 0.2, 0.2], "cellSize": 0.1, "heights": [[0.2, 0.2]] * 3,
         "rigidity": "soft", "compressibility": 2.0}


def load(item):
    return load_scenario({"container": {"dims": [1, 1, 1]}, "items": [item]})[1][0]


def test_soft_item_is_squashed_along_height():
    it = load(SHIRT)
    assert it.dims == (0.3, 0.2, 0.1) and it.compressibility_k == 2.0
    assert abs(it.true_volume - 0.006) < 1e-9 and it.scan_shape == "box"


def test_rigid_item_ignores_stray_k():
    assert load(dict(SHIRT, id="mug", rigidity="rigid")).dims == (0.3, 0.2, 0.2)
    assert load(dict(SHIRT, id="loose")).compressibility_k == 2.0  # no rigidity key: trust k


def test_server_document_fields_reach_the_item():
    it = load(dict(SHIRT, id="glass", rigidity="fragile", keepUpright=True, mass=0.4))
    assert it.fragile and it.keep_upright and it.mass == 0.4 and it.compressibility_k == 1.0
    assert not load(SHIRT).fragile and not load(SHIRT).keep_upright


def test_compressed_item_fits_where_loose_would_not():
    box = Container("c", (1, 1, 0.1))
    loose = Item.box("shirt", 0.3, 0.2, 0.2)
    assert not loose.fits_in(box) and loose.compressed(2.0).fits_in(box)
    r = pack_naive(box, [loose.compressed(2.0)])
    assert [p.item_id for p in r.placements] == ["shirt"] and verify(r, [loose.compressed(2.0)]) == []
    assert loose.compressed(1.0) is loose
