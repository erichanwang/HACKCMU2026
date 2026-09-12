"""verify() is the last thing between a ranked candidate plan and a laptop through a shoe.

Every test here hand-builds a *violating* PackResult -- no solver involved -- and pins both
that verify() rejects it and that the message names the items a human would have to look at.
The nesting block additionally pins the other direction: a genuine nest inside a scanned
cavity must stay clean, or the decoder's cavity packing would be unusable.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packer3d import Container, Item, Obstacle, PackResult, Placement, verify  # noqa: E402
from packer3d.verify import UPRIGHT_ORIENTATIONS  # noqa: E402


def place(item, pos, orientation="xyz", dims=None, mass=None, fragile=None):
    d = tuple(float(v) for v in (dims if dims is not None else item.dims))
    p = tuple(float(v) for v in pos)
    return Placement(item.id, item.shape, p, d, tuple(p[k] + d[k] / 2.0 for k in range(3)),
                     orientation, None, item.radius, item.height,
                     item.mass if mass is None else mass,
                     item.fragile if fragile is None else fragile,
                     item.volume, item.priority)


def result(container, placements, unpacked=()):
    return PackResult("hand-built", container, list(placements), [dict(u) for u in unpacked], {})


def only(errors, needle):
    """The single error containing ``needle`` (asserting exactly one, for a readable failure)."""
    hits = [e for e in errors if needle in e]
    assert len(hits) == 1, f"expected exactly one {needle!r} error, got {errors}"
    return hits[0]


BOX = Container("suitcase", (1.0, 1.0, 1.0), gravity=True, min_support=0.7)


# ------------------------------------------------------------------ the invariants
def test_overlap_is_rejected_and_names_both_items():
    a = Item.box("laptop", 0.4, 0.3, 0.1, mass=1.0)
    b = Item.box("charger", 0.2, 0.2, 0.1, mass=0.5)
    r = result(BOX, [place(a, (0, 0, 0)), place(b, (0.3, 0.1, 0))])
    msg = only(verify(r, [a, b]), "overlaps")
    assert "laptop" in msg and "charger" in msg
    assert "0.1 m along" in msg            # says how deep, and along which axis


def test_one_bad_placement_is_one_overlap_message_not_one_per_sub_box():
    bowl = _bowl()                          # decomposes into 16 sub-boxes
    slab = Item.box("slab", 0.2, 0.2, 0.05, mass=1.0)
    r = result(BOX, [place(bowl, (0, 0, 0)), place(slab, (0, 0, 0.0))])
    assert len([e for e in verify(r, [bowl, slab]) if "overlaps" in e]) == 1


def test_a_floating_item_is_rejected_and_says_what_is_under_it():
    a = Item.box("book", 0.3, 0.3, 0.1, mass=1.0)
    b = Item.box("mug", 0.1, 0.1, 0.1, mass=0.3)
    r = result(BOX, [place(a, (0, 0, 0)), place(b, (0, 0, 0.5))])
    msg = only(verify(r, [a, b]), "not supported")
    assert "mug" in msg and "book" in msg
    assert "support ratio 0.000" in msg and "0.4 m gap" in msg


def test_a_floating_item_over_empty_floor_says_nothing_is_under_it():
    a = Item.box("mug", 0.1, 0.1, 0.1, mass=0.3)
    r = result(BOX, [place(a, (0.4, 0.4, 0.5))])
    assert "nothing is under it" in only(verify(r, [a]), "not supported")


def test_partial_support_below_min_support_is_rejected():
    base = Item.box("base", 0.4, 0.4, 0.2, mass=1.0)
    top = Item.box("tower", 0.4, 0.4, 0.2, mass=1.0)   # only 1/4 of it lands on base
    r = result(BOX, [place(base, (0, 0, 0)), place(top, (0.2, 0.2, 0.2))])
    msg = only(verify(r, [base, top]), "not supported")
    assert "tower" in msg and "support ratio 0.250" in msg


def test_load_on_a_fragile_item_is_rejected_and_names_both():
    egg = Item.box("eggs", 0.3, 0.3, 0.2, mass=0.5, fragile=True)
    tin = Item.box("tinned_soup", 0.3, 0.3, 0.2, mass=3.0)
    r = result(BOX, [place(egg, (0, 0, 0)), place(tin, (0, 0, 0.2))])
    msg = only(verify(r, [egg, tin]), "rests on fragile")
    assert "tinned_soup rests on fragile eggs" in msg and "contact area 0.09" in msg


def test_a_tipped_keep_upright_item_is_rejected_by_name():
    wine = Item.box("wine", 0.1, 0.1, 0.4, mass=1.2, keep_upright=True)
    r = result(BOX, [place(wine, (0, 0, 0), orientation="zyx", dims=(0.4, 0.1, 0.1))])
    msg = only(verify(r, [wine]), "keep-upright item was tipped")
    assert "wine" in msg and "'zyx'" in msg


def test_a_tipped_keep_upright_cylinder_is_rejected_too():
    can = Item.cylinder("thermos", 0.05, 0.3, mass=1.0, keep_upright=True)
    r = result(BOX, [place(can, (0, 0, 0), orientation="cyl_axis_x", dims=(0.3, 0.1, 0.1))])
    assert "thermos" in only(verify(r, [can]), "keep-upright item was tipped")


def test_upright_orientations_are_exactly_the_ones_that_keep_item_z_up():
    assert UPRIGHT_ORIENTATIONS == frozenset({"xyz", "yxz", "cyl_axis_z"})


def test_escaping_the_container_is_rejected_with_the_span_that_escaped():
    a = Item.box("tripod", 0.4, 0.3, 0.2, mass=1.0)
    r = result(BOX, [place(a, (0.8, 0, 0))])
    msg = only(verify(r, [a]), "outside the container")
    assert "tripod" in msg and "suitcase" in msg and "0.8..1.2" in msg


def test_mass_over_the_container_limit_is_rejected_and_blames_the_heaviest():
    light = Container("suitcase", (1.0, 1.0, 1.0), gravity=True, max_mass=5.0)
    a = Item.box("dumbbell", 0.3, 0.3, 0.3, mass=6.0)
    b = Item.box("socks", 0.3, 0.3, 0.3, mass=0.2)
    r = result(light, [place(a, (0, 0, 0)), place(b, (0.4, 0, 0))])
    msg = only(verify(r, [a, b]), "exceeds max_mass")
    assert "dumbbell=6" in msg and "suitcase" in msg and "by 1.2" in msg


def test_an_item_inside_an_obstacle_is_rejected_by_obstacle_id():
    c = Container("boot", (1.0, 1.0, 1.0), gravity=True,
                  obstacles=[Obstacle("wheel_arch", (0, 0, 0), (0.3, 0.3, 0.3))])
    a = Item.box("bag", 0.4, 0.4, 0.2, mass=1.0)
    r = result(c, [place(a, (0.1, 0.1, 0))])
    assert "obstacle wheel_arch" in only(verify(r, [a]), "overlaps")


def test_a_valid_hand_built_stack_passes_clean():
    base = Item.box("base", 0.5, 0.5, 0.2, mass=2.0)
    top = Item.box("top", 0.4, 0.4, 0.2, mass=1.0)
    r = result(BOX, [place(base, (0, 0, 0)), place(top, (0.05, 0.05, 0.2))])
    assert verify(r, [base, top]) == []


# ------------------------------------------------------------------ nesting
def _bowl(fragile=False):
    """A 20x20x10 cm scanned bowl: a full-height rim around a 10x10 cm, 1 cm-deep interior."""
    grid = [[10, 10, 10, 10], [10, 1, 1, 10], [10, 1, 1, 10], [10, 10, 10, 10]]
    return Item.from_scanned_heightmap(
        {"id": "bowl", "width": 20.0, "depth": 20.0, "height": 10.0, "cellSize": 5.0, "heights": grid},
        mass=0.4, fragile=fragile)


CUP = Item.box("cup", 0.08, 0.08, 0.06, mass=0.2)


def test_an_item_in_a_scanned_cavity_nests_legally():
    bowl = _bowl()
    r = result(BOX, [place(bowl, (0, 0, 0)), place(CUP, (0.06, 0.06, 0.01))])
    assert verify(r, [bowl, CUP]) == [], "a genuine nest must not be reported as an overlap"


def test_the_same_item_shoved_into_the_rim_is_a_collision_not_a_nest():
    bowl = _bowl()
    r = result(BOX, [place(bowl, (0, 0, 0)), place(CUP, (0.0, 0.06, 0.01))])
    msg = only(verify(r, [bowl, CUP]), "overlaps")
    assert "bowl" in msg and "cup" in msg and "not a nest" in msg


def test_interpenetration_without_a_scan_is_always_an_overlap():
    """No height grid means no known cavity: verify never invents a hole to excuse a clash."""
    from dataclasses import replace
    solid_bowl = replace(_bowl(), height_grid=None, scan_shape=None)
    r = result(BOX, [place(solid_bowl, (0, 0, 0)), place(CUP, (0.06, 0.06, 0.01))])
    assert "cup" in only(verify(r, [solid_bowl, CUP]), "overlaps")


def test_nesting_into_a_fragile_scanned_item_is_still_rejected():
    bowl = _bowl(fragile=True)
    r = result(BOX, [place(bowl, (0, 0, 0)), place(CUP, (0.06, 0.06, 0.01))])
    assert "cup rests on fragile bowl" in only(verify(r, [bowl, CUP]), "rests on fragile")


def test_an_item_bridging_a_scanned_cavity_is_not_supported_by_the_hole():
    """The rim of a bowl holds a lid; the hole in the middle does not."""
    bowl = _bowl()
    lid = Item.box("lid", 0.08, 0.08, 0.02, mass=0.3)
    r = result(BOX, [place(bowl, (0, 0, 0)), place(lid, (0.06, 0.06, 0.10))])
    msg = only(verify(r, [bowl, lid]), "not supported")
    assert "lid" in msg and "bowl" in msg and "0.09 m gap" in msg


# ------------------------------------------------------------------ bookkeeping
def test_an_item_that_is_neither_placed_nor_unpacked_is_reported():
    a = Item.box("ghost", 0.1, 0.1, 0.1, mass=0.1)
    assert any("ghost" in e and "neither placed nor" in e for e in verify(result(BOX, []), [a]))


def test_verify_never_raises_on_a_nan_placement():
    a = Item.box("nan", 0.1, 0.1, 0.1, mass=0.1)
    r = result(BOX, [place(a, (float("nan"), 0, 0))])
    assert any("non-finite" in e for e in verify(r, [a]))
