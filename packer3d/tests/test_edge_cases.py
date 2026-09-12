"""Edge-case suite for packer3d.  Every packing produced here is re-checked with verify()."""
import json
import math
import time

import numpy as np
import pytest

from packer3d import (Container, DecoderParams, Item, Obstacle, ObjectiveWeights, OptimizerConfig,
                      balance_masses, exhaustive_small, gap_report, load_scenario, lower_bounds,
                      pack_naive, pack_optimized, verify)
from packer3d.decoder import REASON_MASS, REASON_NO_SPACE, REASON_TOO_BIG, PackState

FAST = OptimizerConfig(time_budget_s=0, max_iterations=60, seed=0)


def box_container(L=10, W=10, H=10, **kw):
    return Container("c", (L, W, H), **kw)


def packed_ids(r):
    return {p.item_id for p in r.placements}


def assert_valid(r, items):
    errs = verify(r, items)
    assert errs == [], errs


# ---------------------------------------------------------------- validation
def test_empty_item_list():
    r = pack_optimized(box_container(), [], FAST)
    assert_valid(r, [])
    assert r.placements == [] and r.unpacked == []
    assert r.metrics["volume_utilization"] == 0.0
    json.loads(r.to_json())


def test_negative_or_zero_dims_rejected():
    with pytest.raises(ValueError):
        Item.box("a", -1, 1, 1)
    with pytest.raises(ValueError):
        Item.box("sheet", 1, 1, 0)          # zero-thickness sheet
    with pytest.raises(ValueError):
        Item.cylinder("c", 0, 1)


def test_nan_inf_rejected():
    with pytest.raises(ValueError):
        Item.box("a", float("nan"), 1, 1)
    with pytest.raises(ValueError):
        Item.box("a", 1, float("inf"), 1)
    with pytest.raises(ValueError):
        Item.box("a", 1, 1, 1, mass=float("nan"))


def test_negative_mass_rejected():
    with pytest.raises(ValueError):
        Item.box("a", 1, 1, 1, mass=-2)


def test_priority_must_be_positive():
    with pytest.raises(ValueError):
        Item.box("a", 1, 1, 1, priority=0)
    with pytest.raises(ValueError):
        Item.box("a", 1, 1, 1, priority=-1)


def test_duplicate_ids_rejected():
    items = [Item.box("a", 1, 1, 1), Item.box("a", 2, 2, 2)]
    with pytest.raises(ValueError):
        pack_optimized(box_container(), items, FAST)
    with pytest.raises(ValueError):
        pack_naive(box_container(), items)


def test_bad_container_rejected():
    with pytest.raises(ValueError):
        Container("c", (0, 1, 1))
    with pytest.raises(ValueError):
        Container("c", (1, 2, 1), shape="cylinder")          # not (2R, 2R, H)
    with pytest.raises(ValueError):
        Container("c", (1, 1, 1), shape="sphere")
    with pytest.raises(ValueError):
        Container("c", (1, 1, 1), max_mass=-1)
    with pytest.raises(ValueError):
        Container("c", (1, 1, 1), obstacles=[Obstacle("o", (0.5, 0, 0), (1, 1, 1))])  # sticks out
    with pytest.raises(ValueError):
        Container("c", (1, 1, 1), min_support=1.5)


# ---------------------------------------------------------------- fit / rotation
def test_item_larger_than_container_in_every_orientation():
    items = [Item.box("big", 11, 1, 1), Item.box("ok", 1, 1, 1)]
    r = pack_optimized(box_container(), items, FAST)
    assert_valid(r, items)
    assert packed_ids(r) == {"ok"}
    assert r.unpacked[0]["id"] == "big" and r.unpacked[0]["reason"] == REASON_TOO_BIG


def test_item_fits_only_after_rotation():
    items = [Item.box("plank", 9, 1, 1)]
    c = Container("c", (2, 2, 10))             # only fits standing up (z)
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    assert packed_ids(r) == {"plank"}
    assert r.placements[0].dims[2] == pytest.approx(9)


def test_keep_upright_removes_only_fitting_rotation():
    items = [Item.box("plank", 9, 1, 1, keep_upright=True)]
    c = Container("c", (2, 2, 10))
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    assert packed_ids(r) == set()
    assert r.unpacked[0]["reason"] == REASON_TOO_BIG


def test_exact_fit_single_item():
    items = [Item.box("cube", 10, 10, 10, mass=1)]
    r = pack_optimized(box_container(), items, FAST)
    assert_valid(r, items)
    assert r.metrics["volume_utilization"] == pytest.approx(1.0)


def test_exact_tiling_eight_cubes():
    items = [Item.box(f"c{i}", 5, 5, 5, mass=1) for i in range(8)]
    for fn in (pack_naive, lambda c, i: pack_optimized(c, i, FAST)):
        r = fn(box_container(), items)
        assert_valid(r, items)
        assert len(r.placements) == 8
        assert r.metrics["volume_utilization"] == pytest.approx(1.0)


def test_one_item_too_many_is_never_squeezed_in():
    items = [Item.box(f"c{i}", 5, 5, 5, mass=1) for i in range(9)]
    r = pack_optimized(box_container(), items, FAST)
    assert_valid(r, items)
    assert len(r.placements) == 8 and len(r.unpacked) == 1
    assert r.unpacked[0]["reason"] == REASON_NO_SPACE


# ---------------------------------------------------------------- mass
def test_mass_limit_never_exceeded():
    items = [Item.box(f"b{i}", 2, 2, 2, mass=3) for i in range(10)]
    r = pack_optimized(box_container(max_mass=10), items, FAST)
    assert_valid(r, items)
    assert r.metrics["total_mass"] <= 10 + 1e-9
    assert len(r.placements) == 3
    assert all(u["reason"] == REASON_MASS for u in r.unpacked)


def test_mass_limit_drops_low_priority_first():
    items = [Item.box("low", 2, 2, 2, mass=6, priority=1), Item.box("high", 2, 2, 2, mass=6, priority=5)]
    r = pack_optimized(box_container(max_mass=6), items, FAST)
    assert_valid(r, items)
    assert packed_ids(r) == {"high"}


def test_max_mass_zero_only_massless_items():
    items = [Item.box("ghost", 1, 1, 1, mass=0), Item.box("heavy", 1, 1, 1, mass=0.001)]
    r = pack_optimized(box_container(max_mass=0), items, FAST)
    assert_valid(r, items)
    assert packed_ids(r) == {"ghost"}


# ---------------------------------------------------------------- fragile / gravity
def _fragile_scenario(gravity):
    c = Container("c", (2, 2, 10), gravity=gravity)          # footprint fits exactly one 2x2 column
    items = [Item.box("glass", 2, 2, 2, mass=1, fragile=True)] + [Item.box(f"b{i}", 2, 2, 2, mass=1) for i in range(3)]
    return c, items


def test_fragile_nothing_on_top_gravity():
    c, items = _fragile_scenario(True)
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    glass = next(p for p in r.placements if p.item_id == "glass")
    assert glass.position[2] == pytest.approx(6.0)           # forced to the top of the stack


def test_fragile_nothing_on_top_microgravity():
    c, items = _fragile_scenario(False)
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    glass = next(p for p in r.placements if p.item_id == "glass")
    others = [p for p in r.placements if p.item_id != "glass"]
    assert all(abs(p.position[2] - (glass.position[2] + 2)) > 1e-6 for p in others)


def test_gravity_forbids_floating():
    items = [Item.box("base", 4, 4, 2, mass=1), Item.box("top", 4, 4, 2, mass=1)]
    c = Container("c", (4, 4, 10), gravity=True)
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    for p in r.placements:
        assert p.position[2] == pytest.approx(0.0) or p.position[2] == pytest.approx(2.0)


def test_gravity_min_support_respected():
    # small pedestal, big slab: the slab would only be 25 % supported -> must sit on the floor
    items = [Item.box("pedestal", 2, 2, 2, mass=1), Item.box("slab", 4, 4, 1, mass=1)]
    c = Container("c", (4, 4, 10), gravity=True, min_support=0.7)
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    slab = next(p for p in r.placements if p.item_id == "slab")
    assert slab.position[2] == pytest.approx(0.0)


def test_microgravity_any_height_no_overlap():
    items = [Item.box(f"b{i}", 1, 1, 1, mass=1) for i in range(6)]
    c = Container("c", (1, 1, 10), gravity=False)
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    assert len(r.placements) == 6


# ---------------------------------------------------------------- obstacles
def test_obstacle_avoided_and_verified():
    c = Container("c", (10, 10, 10), obstacles=[Obstacle("post", (0, 0, 0), (2, 2, 10))])
    items = [Item.box(f"b{i}", 4, 4, 2, mass=1) for i in range(4)]
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    assert len(r.placements) == 4


def test_obstacle_acts_as_support():
    c = Container("c", (2, 2, 10), gravity=True, obstacles=[Obstacle("shelf", (0, 0, 0), (2, 2, 3))])
    items = [Item.box("b", 2, 2, 2, mass=1)]
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    assert r.placements[0].position[2] == pytest.approx(3.0)


def test_obstacle_covering_whole_floor():
    c = Container("c", (10, 10, 10), gravity=True, obstacles=[Obstacle("floor", (0, 0, 0), (10, 10, 1))])
    items = [Item.box(f"b{i}", 3, 3, 3, mass=1) for i in range(5)]
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    assert len(r.placements) == 5
    assert all(p.position[2] >= 1 - 1e-9 for p in r.placements)


# ---------------------------------------------------------------- cylinders
def test_cylinder_lies_down_when_needed():
    items = [Item.cylinder("tube", radius=0.5, height=8, mass=1)]
    c = Container("c", (10, 2, 2))
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    p = r.placements[0]
    assert p.axis == "x" and p.orientation == "cyl_axis_x"
    assert p.dims == pytest.approx((8, 1, 1))


def test_cylinder_keep_upright_blocks_lay_down():
    items = [Item.cylinder("tube", radius=0.5, height=8, mass=1, keep_upright=True),
             Item.cylinder("tube2", radius=0.5, height=8, mass=1, allow_lay_down=False)]
    c = Container("c", (10, 2, 2))
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    assert packed_ids(r) == set()


def test_cylinder_true_volume_in_metrics():
    items = [Item.cylinder("drum", radius=1, height=2, mass=1)]
    c = Container("c", (2, 2, 2))
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    assert r.metrics["volume_utilization"] == pytest.approx(math.pi * 2 / 8)
    d = r.to_dict()["placements"][0]
    assert d["radius"] == 1 and d["height"] == 2 and d["axis"] == "z"


def test_cylindrical_container_corners_inside_circle():
    c = Container("drum", (4, 4, 4), shape="cylinder", gravity=True)
    items = [Item.box(f"b{i}", 1, 1, 1, mass=1) for i in range(20)]
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    assert len(r.placements) >= 12
    for p in r.placements:
        for cx in (p.position[0], p.position[0] + p.dims[0]):
            for cy in (p.position[1], p.position[1] + p.dims[1]):
                assert (cx - 2) ** 2 + (cy - 2) ** 2 <= 4 + 1e-6


def test_cylindrical_container_rejects_wide_plank():
    c = Container("drum", (4, 4, 2), shape="cylinder")   # too low for the plank to stand up
    items = [Item.box("plank", 4, 0.5, 0.5, mass=1), Item.box("diag", 3.9, 0.5, 0.5, mass=1)]
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    assert packed_ids(r) == {"diag"}           # 4.0 x 0.5 cannot fit; 3.9 x 0.5 can (3.93 diagonal)


# ---------------------------------------------------------------- mass properties
def test_all_items_massless_uses_volume_centroid():
    items = [Item.box(f"b{i}", 2, 2, 2) for i in range(4)]
    r = pack_optimized(box_container(gravity=False), items, FAST)
    assert_valid(r, items)
    assert r.metrics["com_basis"] == "volume_centroid"
    assert all(math.isfinite(v) for v in r.metrics["com"])


def test_identical_items_balancing_moves_com():
    # two identical slots side by side, very different masses: balancing must pick the better layout
    c = Container("c", (4, 2, 2), gravity=True, com_target=(2, 1, 0))
    items = [Item.box("light", 2, 2, 2, mass=1), Item.box("heavy", 2, 2, 2, mass=9)]
    r = pack_optimized(c, items, OptimizerConfig(time_budget_s=0, max_iterations=0, seed=0, balance=False))
    assert_valid(r, items)
    before = r.metrics["com_lateral_offset"]
    swaps = balance_masses(c, r.placements)
    assert swaps == 0  # both layouts are symmetric around the target -> no gain
    # asymmetric target: balancing should now prefer the heavy item near x=0.5
    c2 = Container("c", (4, 2, 2), gravity=True, com_target=(0.5, 1, 0))
    r2 = pack_optimized(c2, items, OptimizerConfig(time_budget_s=0, max_iterations=0, seed=0, balance=False))
    heavy = next(p for p in r2.placements if p.item_id == "heavy")
    if heavy.position[0] > 1:  # heavy sits at x in [2,4]: a swap improves the CoM
        assert balance_masses(c2, r2.placements) == 1
        heavy = next(p for p in r2.placements if p.item_id == "heavy")
    assert heavy.position[0] == pytest.approx(0.0)
    assert_valid(r2, items)


def test_balancing_preserves_geometry_and_validity():
    rng = np.random.default_rng(1)
    items = [Item.box(f"b{i}", 1, 1, 1, mass=float(rng.uniform(1, 10))) for i in range(27)]
    c = Container("c", (3, 3, 3), gravity=True)
    r = pack_optimized(c, items, OptimizerConfig(time_budget_s=0, max_iterations=0, seed=0, balance=False))
    assert_valid(r, items)
    before = r.metrics["com_lateral_offset"]
    swaps = balance_masses(c, r.placements)
    assert swaps > 0
    r2 = pack_optimized(c, items, OptimizerConfig(time_budget_s=0, max_iterations=0, seed=0, balance=True))
    assert_valid(r2, items)
    assert r2.metrics["com_lateral_offset"] < before


# ---------------------------------------------------------------- scale / determinism / contract
def test_many_tiny_items_fast_and_verified():
    items = [Item.box(f"t{i}", 1, 1, 1, mass=1) for i in range(300)]
    c = Container("c", (10, 10, 10), gravity=True)
    t = time.perf_counter()
    r = pack_naive(c, items)
    assert time.perf_counter() - t < 3.0
    assert_valid(r, items)
    assert len(r.placements) == 300


def test_determinism_with_iteration_budget():
    c, items, _, w = load_scenario("examples/suitcase.json")
    cfg = OptimizerConfig(time_budget_s=0, max_iterations=40, seed=7)
    a = pack_optimized(c, items, cfg, weights=w).to_dict()
    b = pack_optimized(c, items, cfg, weights=w).to_dict()
    a.pop("stats"), b.pop("stats")          # stats carry wall-clock timings
    assert json.dumps(a) == json.dumps(b)


def test_example_scenarios_verify_for_both_strategies():
    for path in ("examples/dragon_resupply.json", "examples/suitcase.json"):
        c, items, _, w = load_scenario(path)
        assert_valid(pack_naive(c, items), items)
        r = pack_optimized(c, items, OptimizerConfig(time_budget_s=0, max_iterations=30, seed=0), weights=w)
        assert_valid(r, items)


def test_json_output_contract():
    c, items, _, w = load_scenario("examples/dragon_resupply.json")
    r = pack_optimized(c, items, OptimizerConfig(time_budget_s=0, max_iterations=5), weights=w)
    d = json.loads(r.to_json())
    assert d["strategy"] == "optimized"
    assert set(d["container"]) >= {"id", "shape", "dims", "obstacles", "com_target"}
    for p in d["placements"]:
        assert set(p) >= {"item_id", "shape", "position", "dims", "center", "orientation", "mass", "fragile"}
        if p["shape"] == "cylinder":
            assert {"axis", "radius", "height"} <= set(p)
            assert p["axis"] in "xyz"
        else:
            assert p["orientation"] in ("xyz", "xzy", "yxz", "yzx", "zxy", "zyx")
    assert set(d["metrics"]) >= {"volume_utilization", "com", "com_lateral_offset", "max_height_fraction", "total_mass"}


def test_verify_catches_tampering():
    items = [Item.box("a", 2, 2, 2, mass=1), Item.box("b", 2, 2, 2, mass=1)]
    r = pack_optimized(box_container(), items, FAST)
    assert_valid(r, items)
    r.placements[1].position = r.placements[0].position   # overlap
    r.placements[1].center = r.placements[0].center
    assert any("overlaps" in e for e in verify(r, items))
    r.placements[1].position = (0, 0, 5)                   # floating under gravity
    r.placements[1].center = (1, 1, 6)
    assert any("not supported" in e for e in verify(r, items))


def test_lower_bounds_and_gap_report(capsys):
    c = box_container(max_mass=5)
    items = [Item.box(f"b{i}", 10, 10, 2, mass=2) for i in range(6)]   # volume allows 5, mass allows 2
    lb = lower_bounds(c, items)
    assert lb["min_unpacked_by_volume"] == 1 and lb["min_unpacked_by_mass"] == 4
    assert lb["min_unpacked_lower_bound"] == 4
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    rep = gap_report(r, items)
    assert rep["optimal_on_count"] is True
    assert "OPTIMAL" in capsys.readouterr().out


def test_exhaustive_small_matches_decoder_and_verifies():
    items = [Item.box("a", 3, 2, 1, mass=2), Item.box("b", 2, 2, 2, mass=1),
             Item.cylinder("c", 0.5, 2, mass=3), Item.box("d", 1, 1, 3, mass=1)]
    c = Container("c", (4, 4, 4), gravity=True)
    ex = exhaustive_small(c, items)
    assert_valid(ex, items)
    assert ex.stats["sequences_tried"] == 24
    assert len(ex.placements) == 4
    opt = pack_optimized(c, items, OptimizerConfig(time_budget_s=0, max_iterations=100, seed=0))
    assert_valid(opt, items)
    assert opt.metrics["objective"] <= ex.metrics["objective"] * 1.1 + 1e-9
    with pytest.raises(ValueError):
        exhaustive_small(c, [Item.box(f"x{i}", 1, 1, 1) for i in range(8)])


def test_floating_point_flush_positions_are_rounded():
    items = [Item.box(f"b{i}", 0.1, 0.1, 0.1, mass=0.1) for i in range(30)]
    c = Container("c", (0.3, 0.3, 0.3), gravity=True)
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    assert len(r.placements) == 27
    for p in r.placements:
        for v in p.position:
            assert abs(v * 10 - round(v * 10)) < 1e-9


def test_from_scan_lidar_payload():
    scans = [
        {"id": "s1", "shape": "box", "length": 0.3, "depth": 0.2, "height": 0.1},
        {"id": "s2", "shape": "Cylinder", "length": 0.1, "depth": 0.1, "height": 0.3},
        {"id": "s3", "shape": "cylinder", "length": 0.12, "depth": 0.1, "height": 0.2},  # slightly noisy scan
    ]
    items = [Item.from_scan(**s) for s in scans]
    assert items[0].dims == (0.3, 0.2, 0.1)
    assert items[1].shape == "cylinder" and items[1].radius == pytest.approx(0.05)
    assert items[2].radius == pytest.approx(0.05)          # radius from the smaller footprint side
    odd = Item.from_scan("mannequin", "irregular", 0.3, 0.2, 0.4)   # packs as bbox, nothing on top
    assert odd.shape == "box" and odd.fragile and odd.scan_shape == "irregular"
    c = Container("bin", (0.5, 0.5, 0.5), gravity=True)
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    assert len(r.placements) == 3
    assert r.metrics["com_basis"] == "volume_centroid"      # no masses from the scanner
    c2, items2, _, _ = load_scenario({"container": {"dims": [0.5, 0.5, 0.5]}, "items": scans})
    assert [i.dims for i in items2] == [i.dims for i in items]


def _cube_mesh(dx, dy, dz, yaw_deg=0.0):
    v = np.array([[x, y, z] for z in (0, dz) for y in (0, dy) for x in (0, dx)], dtype=float)
    f = np.array([[0, 1, 3], [0, 3, 2], [4, 7, 5], [4, 6, 7], [0, 4, 5], [0, 5, 1],
                  [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    t = math.radians(yaw_deg)
    R = np.array([[math.cos(t), -math.sin(t), 0], [math.sin(t), math.cos(t), 0], [0, 0, 1]])
    return v @ R.T, f


def _cylinder_mesh(r, h, n=64):
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    ring0 = np.column_stack([r * np.cos(t), r * np.sin(t), np.zeros(n)])
    ring1 = ring0 + [0, 0, h]
    v = np.vstack([ring0, ring1, [[0, 0, 0]], [[0, 0, h]]])
    c0, c1 = 2 * n, 2 * n + 1
    f = []
    for i in range(n):
        j = (i + 1) % n
        f += [[i, j, n + i], [j, n + j, n + i], [c0, j, i], [c1, n + i, n + j]]
    return v, np.array(f)


def test_from_mesh_classifies_and_finds_tight_bbox():
    v, f = _cube_mesh(0.3, 0.2, 0.1, yaw_deg=30)          # scanned at an angle
    box = Item.from_mesh("crate", v, f)
    assert box.shape == "box" and box.scan_shape == "box"
    assert sorted(box.dims[:2]) == pytest.approx([0.2, 0.3], abs=2e-3)
    assert box.scan_yaw_deg == pytest.approx(60, abs=1.5) or box.scan_yaw_deg == pytest.approx(30, abs=1.5)
    assert box.volume == pytest.approx(0.006, rel=1e-6)
    v, f = _cylinder_mesh(0.05, 0.3)
    can = Item.from_mesh("can", v, f)
    assert can.shape == "cylinder" and can.radius == pytest.approx(0.05, abs=2e-3)
    assert can.volume == pytest.approx(math.pi * 0.05 ** 2 * 0.3, rel=0.01)
    # a thin diagonal slab: bbox much bigger than its volume -> irregular, fragile, bbox-packed
    v, f = _cube_mesh(0.3, 0.02, 0.1, yaw_deg=45)
    odd = Item.from_mesh("blade", v, f, yaw_search_deg=90)   # no yaw search -> bloated box
    assert odd.shape == "box" and odd.scan_shape == "irregular" and odd.fragile
    assert odd.true_volume == pytest.approx(0.3 * 0.02 * 0.1, rel=1e-6)
    items = [box, can, odd]
    c = Container("bin", (0.5, 0.5, 0.5), gravity=True)
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    assert len(r.placements) == 3
    d = json.loads(r.to_json())
    blade = next(p for p in d["placements"] if p["item_id"] == "blade")
    assert blade["scan_shape"] == "irregular" and "scan_yaw_deg" in blade
    assert r.metrics["packed_volume"] == pytest.approx(sum(i.volume for i in items))
    with pytest.raises(ValueError):
        Item.from_mesh("flat", np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]]))


def test_from_scanned_heightmap_box_cylinder_irregular():
    # a plain box: heightmap ~ uniform full height everywhere
    box_scan = {"id": "crate", "width": 20.0, "depth": 10.0, "height": 8.0, "cellSize": 1.0,
                "heights": [[8.0] * 10 for _ in range(20)]}
    box = Item.from_scanned_heightmap(box_scan)
    assert box.shape == "box" and box.scan_shape == "box" and not box.fragile
    assert box.dims == pytest.approx((0.20, 0.10, 0.08))
    assert box.true_volume == pytest.approx(0.20 * 0.10 * 0.08, rel=1e-6)

    # a cylinder: circular footprint over a square bbox (build via distance-from-center mask)
    n = 30
    cell = 1.0
    r = n / 2.0
    heights = [[8.0 if (i - r + 0.5) ** 2 + (j - r + 0.5) ** 2 <= r * r else 0.0 for j in range(n)]
               for i in range(n)]
    cyl_scan = {"id": "can", "width": n * cell, "depth": n * cell, "height": 8.0, "cellSize": cell,
                "heights": heights}
    can = Item.from_scanned_heightmap(cyl_scan)
    assert can.shape == "cylinder" and can.scan_shape == "cylinder"
    assert can.radius == pytest.approx(0.15, abs=0.01)

    # an L-shaped bracket: one quadrant missing -> irregular, fragile by default
    heights = [[8.0] * 10 for _ in range(20)]
    for i in range(10, 20):
        for j in range(0, 5):
            heights[i][j] = 0.0
    l_scan = {"id": "bracket", "width": 20.0, "depth": 10.0, "height": 8.0, "cellSize": 1.0, "heights": heights}
    bracket = Item.from_scanned_heightmap(l_scan)
    assert bracket.scan_shape == "irregular" and bracket.fragile
    assert bracket.true_volume < bracket.bbox_volume

    items = [box, can, bracket]
    c = Container("bin", (0.6, 0.6, 0.6), gravity=True)
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    assert len(r.placements) == 3

    # units="m" path and JSON scenario routing
    box_m = dict(box_scan)
    box_m["width"], box_m["depth"], box_m["height"] = 0.2, 0.1, 0.08
    box_m["cellSize"] = 0.01
    box2 = Item.from_scanned_heightmap(box_m, units="m")
    assert box2.dims == pytest.approx(box.dims)
    c2, items2, _, _ = load_scenario({"container": {"dims": [0.6, 0.6, 0.6]}, "items": [box_scan]})
    assert items2[0].scan_shape == "box"
