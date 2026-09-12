"""Regressions for the scenario loader's three item forms and its zone containers.

Each test here is a bug that was in `scenario.py`, not a feature: the loader is the front
door for every LiDAR scan the phone sends, and its three input forms kept disagreeing with
each other about a keyword one of them honoured.
"""
import pytest

from packer3d import pack_naive
from packer3d.models import validate_items
from packer3d.scenario import load_scenario, load_zones

CABIN = (0.40, 0.55, 0.23)   # x across the bag, y wheel end -> handle end, z up

# fixture spelling (tools/packbench/fixtures): width/depth/height/cellSize/heights in CENTIMETRES
SHOE_CM = {"id": "trainer", "width": 30.0, "depth": 12.0, "height": 11.5, "cellSize": 2.0,
           "heights": [[11.5] * 15 for _ in range(6)], "mass": 0.31}


def test_count_on_a_heightmap_payload_makes_distinct_ids():
    # regression: the "heights" branch keyed every copy on d["id"] instead of the generated
    # iid, so `count: 2` built two items called "trainer" and pack_* raised "duplicate item
    # id" -- while the sibling "length" branch a few lines below named them "trainer_1/_2".
    # (Unreachable from the phone -- nothing on the server path sets `count` -- so this is a
    # guard for hand-written scenarios and for tools/packbench fixtures.)
    _, items, _, _ = load_scenario({"container": {"dims": list(CABIN)},
                                    "items": [dict(SHOE_CM, count=3)]})
    assert [i.id for i in items] == ["trainer_1", "trainer_2", "trainer_3"]
    validate_items(items)                                  # used to raise "duplicate item id"
    assert pack_naive(*load_scenario({"container": {"dims": list(CABIN)},
                                      "items": [dict(SHOE_CM, count=3)]})[:2]).metrics["items_packed"] == 3
    # and `count: 1` still leaves the bare id alone (physics/packer3d_adapter.item_metadata
    # re-derives the same names, so the two must agree)
    _, (one,), _, _ = load_scenario({"container": {"dims": list(CABIN)}, "items": [dict(SHOE_CM)]})
    assert one.id == "trainer"


def test_count_names_copies_the_same_way_in_every_form():
    def ids(item):
        return [i.id for i in load_scenario({"container": {"dims": list(CABIN)},
                                             "items": [dict(item, id="x", count=2)]})[1]]
    assert ids(SHOE_CM) == ["x_1", "x_2"]                                        # heightmap form
    assert ids({"length": 0.3, "depth": 0.12, "height": 0.11}) == ["x_1", "x_2"]  # scan form
    assert ids({"shape": "box", "dims": [0.3, 0.12, 0.11]}) == ["x_1", "x_2"]     # dims form
    assert ids({"shape": "cylinder", "radius": 0.04, "height": 0.2}) == ["x_1", "x_2"]


def test_malformed_dims_is_a_loud_error_not_a_silent_truncation():
    # a 4-entry dims used to load as its first 3 entries; a 2-entry one raised IndexError
    for dims in ([0.1, 0.1, 0.1, 0.1], [0.1, 0.1]):
        with pytest.raises(ValueError, match="exactly 3 entries"):
            load_scenario({"container": {"dims": list(CABIN)},
                           "items": [{"id": "b", "shape": "box", "dims": dims}]})


def test_zone_containers_keep_the_declared_com_axis_weights():
    # regression: _zone_container rebuilt the Container without com_axis_weights, so a
    # scenario that declared them silently got the (1, 1, 0.5) default inside every zone.
    scenario = {"container": {"dims": list(CABIN), "com_axis_weights": [1, 1, 0]},
                "zones": [{"id": "interior", "label": "Interior"},
                          {"id": "lid", "label": "Lid", "origin": [0, 0, 0.23], "size": [0.4, 0.55, 0.05]}]}
    base, _, _, _ = load_scenario(scenario)
    assert base.com_axis_weights == (1.0, 1.0, 0.0)
    for _zone, container, _items in load_zones(scenario)[0]:
        assert container.com_axis_weights == (1.0, 1.0, 0.0)
        # com_target stays a per-zone default on purpose: the parent's is an absolute point
        # in the parent's frame, which means nothing once the origin moves.
        assert container.com_target is None
