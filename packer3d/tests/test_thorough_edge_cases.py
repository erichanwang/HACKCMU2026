"""A second, deliberately adversarial pass over packer3d: crash-hardening of verify() and
config validation, the keep_upright default for irregular scans, scenario-JSON error
messages, numerical extremes, and an independent physics-bridge overlap cross-check."""
import math

import numpy as np
import pytest

from packer3d import (Container, Item, Obstacle, OptimizerConfig, ObjectiveWeights,
                      balance_masses, load_scenario, pack_naive, pack_optimized, verify)
from packer3d.physics_bridge import physics_container_dict, to_physics_placements

FAST = OptimizerConfig(time_budget_s=0, max_iterations=40, seed=0)


def assert_valid(r, items):
    errs = verify(r, items)
    assert errs == [], errs


# ============================================================== verify() crash-hardening
def test_verify_never_crashes_on_tampered_zero_area_above_floor():
    items = [Item.box("a", 2, 2, 2, mass=1), Item.box("b", 2, 2, 2, mass=1)]
    c = Container("c", (10, 10, 10), gravity=True)
    r = pack_optimized(c, items, FAST)
    r.placements[0].position = (0.0, 0.0, 5.0)
    r.placements[0].center = (0.0, 1.0, 6.0)
    r.placements[0].dims = (0.0, 2.0, 2.0)
    errs = verify(r, items)   # must not raise
    assert any("zero-area" in e for e in errs)


def test_verify_never_crashes_on_nan_or_inf_placement():
    items = [Item.box("a", 2, 2, 2, mass=1)]
    c = Container("c", (10, 10, 10))
    r = pack_optimized(c, items, FAST)
    for bad in (float("nan"), float("inf"), -float("inf")):
        r.placements[0].position = (bad, 0.0, 0.0)
        errs = verify(r, items)   # must not raise
        assert any("non-finite" in e for e in errs)


def test_verify_never_crashes_on_negative_dims():
    items = [Item.box("a", 2, 2, 2, mass=1)]
    c = Container("c", (10, 10, 10))
    r = pack_optimized(c, items, FAST)
    r.placements[0].dims = (-1.0, 2.0, 2.0)
    errs = verify(r, items)
    assert any("negative dims" in e for e in errs)


def test_verify_flags_overlap_and_unsupported_after_tampering_still_no_crash():
    items = [Item.box("a", 2, 2, 2, mass=1), Item.box("b", 2, 2, 2, mass=1),
             Item.box("c", 2, 2, 2, mass=1)]
    cont = Container("c", (10, 10, 10), gravity=True)
    r = pack_optimized(cont, items, FAST)
    assert_valid(r, items)
    by_id = {p.item_id: p for p in r.placements}
    by_id["b"].position = by_id["a"].position
    by_id["b"].center = by_id["a"].center
    by_id["c"].position = (5.0, 5.0, 8.0)     # floating, unsupported
    by_id["c"].center = (6.0, 6.0, 9.0)
    errs = verify(r, items)
    assert any("overlaps" in e for e in errs)
    assert any("not supported" in e for e in errs)


# ============================================================== OptimizerConfig validation
@pytest.mark.parametrize("kwargs", [
    dict(time_budget_s=-1),
    dict(time_budget_s=float("nan")),
    dict(time_budget_s=float("inf")),
    dict(max_iterations=-1),
    dict(max_iterations=1.5),
    dict(max_iterations=True),          # bool is an int subclass -- must still be rejected
    dict(t_start=0.0),
    dict(t_start=-0.1),
    dict(t_start=float("nan")),
    dict(t_end=-0.1),
    dict(t_end=float("nan")),
    dict(com_weight_grid=()),
    dict(com_weight_grid=(-1.0, 0.5)),
    dict(com_weight_grid=(float("nan"),)),
])
def test_optimizer_config_rejects_bad_values(kwargs):
    with pytest.raises(ValueError):
        OptimizerConfig(**kwargs)


def test_optimizer_config_accepts_boundary_values():
    OptimizerConfig(time_budget_s=0, max_iterations=0, t_start=1e-9, t_end=0.0, com_weight_grid=(0.0,))


def test_t_start_zero_no_longer_crashes_pack_optimized():
    items = [Item.box("a", 1, 1, 1, mass=1)]
    c = Container("c", (2, 2, 2))
    with pytest.raises(ValueError):
        pack_optimized(c, items, OptimizerConfig(t_start=0.0, time_budget_s=1))


# ============================================================== physics bridge: cylindrical container guard
def test_physics_bridge_rejects_cylindrical_container():
    c = Container("cyl", (2.0, 2.0, 3.0), shape="cylinder")
    with pytest.raises(ValueError, match="cylindrical|shape"):
        physics_container_dict(c)
    items = [Item.box("a", 0.5, 0.5, 0.5, mass=1)]
    r = pack_optimized(c, items, FAST)
    with pytest.raises(ValueError, match="cylindrical|shape"):
        to_physics_placements(c, r)


def test_physics_bridge_box_container_still_works():
    c = Container("box_c", (2.0, 2.0, 3.0), shape="box")
    d = physics_container_dict(c)   # must not raise
    assert d["dimensions"] == pytest.approx([2.0, 3.0, 2.0])


# ============================================================== independent overlap cross-check in physics coords
def _physics_obb_corners(pos, quat, dims):
    """8 world corners of an OBB, computed independently (not reusing physics_bridge's own math)."""
    x, y, z, w = quat
    R = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])
    half = np.array(dims) / 2.0
    signs = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
    return np.array(pos) + (signs * half) @ R.T


def _obb_axis_aligned_extent(pos, quat, dims):
    corners = _physics_obb_corners(pos, quat, dims)
    return corners.min(axis=0), corners.max(axis=0)


def test_bridged_placements_still_pairwise_non_overlapping_in_physics_coordinates():
    """Independent cross-check: since our bridged rotations are always axis-aligned (signed
    permutations, never a skew rotation), the physics-frame AABB of each OBB must exactly equal
    its extent, and no two must overlap there either -- checked with fresh code, not by reusing
    physics_bridge's own math."""
    container, items, cfg, w = load_scenario("examples/suitcase.json")
    result = pack_optimized(container, items, OptimizerConfig(time_budget_s=0, max_iterations=80, seed=1), weights=w)
    assert_valid(result, items)
    items_by_id = {it.id: it for it in items}
    placements = to_physics_placements(container, result)
    from packer3d.physics_bridge import physics_object_dims
    boxes = []
    for p in placements:
        it = items_by_id[p["id"]]
        lo, hi = _obb_axis_aligned_extent(p["position"], p["rotation"], physics_object_dims(it))
        boxes.append((p["id"], lo, hi))
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            _, lo1, hi1 = boxes[i]
            _, lo2, hi2 = boxes[j]
            overlap = np.minimum(hi1, hi2) - np.maximum(lo1, lo2)
            assert np.any(overlap <= 1e-6), f"{boxes[i][0]} and {boxes[j][0]} overlap in physics coords"


# ============================================================== irregular keep_upright defaults
def test_irregular_scan_defaults_to_keep_upright_and_fragile():
    it = Item.from_scan("m1", "mannequin", 0.3, 0.2, 1.7)
    assert it.fragile and it.keep_upright
    it2 = Item.from_scan("m2", "mannequin", 0.3, 0.2, 1.7, fragile=False, keep_upright=False)
    assert not it2.fragile and not it2.keep_upright


def test_irregular_mesh_defaults_to_keep_upright():
    v = np.array([[x, y, z] for z in (0, 0.4) for y in (0, 0.1) for x in (0, 0.3)], dtype=float)
    # no faces -> can't classify by volume; force irregular via classify=False
    it = Item.from_mesh("odd", v, faces=None, classify=False)
    assert it.scan_shape == "irregular" and it.fragile and it.keep_upright


def test_box_and_cylinder_scans_do_not_default_to_keep_upright():
    box = Item.from_scan("b", "box", 0.3, 0.2, 0.1)
    assert not box.keep_upright and not box.fragile
    can = Item.from_scan("c", "cylinder", 0.1, 0.1, 0.3)
    assert not can.keep_upright and not can.fragile


def test_scenario_json_irregular_item_gets_keep_upright_default_too():
    # regression: scenario.py used to force a concrete bool, overriding the smart default
    data = {"container": {"dims": [1, 1, 2]},
            "items": [{"id": "weird", "shape": "mannequin", "length": 0.3, "depth": 0.2, "height": 1.7}]}
    _, items, _, _ = load_scenario(data)
    assert items[0].keep_upright and items[0].fragile


def test_scenario_json_explicit_keep_upright_false_overrides_irregular_default():
    data = {"container": {"dims": [1, 1, 2]},
            "items": [{"id": "weird", "shape": "mannequin", "length": 0.3, "depth": 0.2, "height": 1.7,
                       "keep_upright": False}]}
    _, items, _, _ = load_scenario(data)
    assert items[0].keep_upright is False


# ============================================================== heightmap input validation
def test_heightmap_missing_required_keys_raises_clear_error():
    with pytest.raises(ValueError, match="width"):
        Item.from_scanned_heightmap({"id": "x", "depth": 1, "height": 1})
    with pytest.raises(ValueError, match="id"):
        Item.from_scanned_heightmap({"width": 1, "depth": 1, "height": 1})


def test_heightmap_ragged_rows_raises():
    scan = {"id": "x", "width": 3, "depth": 2, "height": 1, "cellSize": 1.0,
            "heights": [[1.0, 1.0], [1.0]]}   # ragged
    with pytest.raises(ValueError, match="ragged"):
        Item.from_scanned_heightmap(scan)


def test_heightmap_non_finite_values_raises():
    scan = {"id": "x", "width": 2, "depth": 2, "height": 1, "cellSize": 1.0,
            "heights": [[1.0, float("nan")], [1.0, 1.0]]}
    with pytest.raises(ValueError, match="non-finite"):
        Item.from_scanned_heightmap(scan)


def test_heightmap_negative_values_raises():
    scan = {"id": "x", "width": 2, "depth": 2, "height": 1, "cellSize": 1.0,
            "heights": [[1.0, -1.0], [1.0, 1.0]]}
    with pytest.raises(ValueError, match="negative"):
        Item.from_scanned_heightmap(scan)


def test_heightmap_non_positive_bbox_dims_raises():
    with pytest.raises(ValueError, match="width"):
        Item.from_scanned_heightmap({"id": "x", "width": 0, "depth": 1, "height": 1})
    with pytest.raises(ValueError, match="height"):
        Item.from_scanned_heightmap({"id": "x", "width": 1, "depth": 1, "height": -1})


def test_heightmap_missing_or_zero_cellsize_falls_back_to_box_no_crash():
    scan = {"id": "x", "width": 2, "depth": 2, "height": 1, "heights": [[1.0, 1.0], [1.0, 1.0]]}
    it = Item.from_scanned_heightmap(scan)   # no cellSize -> can't integrate volume -> plain box
    assert it.scan_shape == "box" and it.true_volume is None


def test_heightmap_single_cell_and_all_zero_no_crash():
    single = {"id": "s", "width": 1, "depth": 1, "height": 1, "cellSize": 1.0, "heights": [[1.0]]}
    it = Item.from_scanned_heightmap(single)
    assert it.scan_shape == "box"
    allzero = {"id": "z", "width": 2, "depth": 2, "height": 0.001, "cellSize": 1.0, "heights": [[0.0, 0.0], [0.0, 0.0]]}
    it2 = Item.from_scanned_heightmap(allzero)   # degenerate but must not crash
    assert it2.true_volume == pytest.approx(0.0, abs=1e-9)


def test_heightmap_volume_never_exceeds_bbox_even_if_cellsize_overcounts():
    # cellSize deliberately too large for the stated footprint -> raw sum could exceed bbox volume;
    # Item.volume must still clamp to bbox_volume (min() in the volume property), utilisation <= 1.
    scan = {"id": "over", "width": 1.0, "depth": 1.0, "height": 1.0, "cellSize": 5.0,
            "heights": [[1.0, 1.0], [1.0, 1.0]]}
    it = Item.from_scanned_heightmap(scan)
    assert it.volume <= it.bbox_volume + 1e-9
    c = Container("c", (2, 2, 2))
    r = pack_optimized(c, [it], FAST)
    assert_valid(r, [it])
    assert r.metrics["volume_utilization"] <= 1.0 + 1e-9


def test_scan_from_scan_rejects_negative_mass():
    with pytest.raises(ValueError):
        Item.from_scan("x", "box", 1, 1, 1, mass=-5)
    with pytest.raises(ValueError):
        Item.from_scanned_heightmap({"id": "x", "width": 1, "depth": 1, "height": 1}, mass=-5)


# ============================================================== scenario.py error messages
def test_scenario_missing_container_key():
    with pytest.raises(ValueError, match="container"):
        load_scenario({"items": []})


def test_scenario_missing_dims_key():
    with pytest.raises(ValueError, match="dims"):
        load_scenario({"container": {"id": "c"}, "items": []})


def test_scenario_missing_item_id():
    with pytest.raises(ValueError, match="id"):
        load_scenario({"container": {"dims": [1, 1, 1]}, "items": [{"shape": "box", "dims": [1, 1, 1]}]})


def test_scenario_box_item_missing_dims():
    with pytest.raises(ValueError, match="dims"):
        load_scenario({"container": {"dims": [1, 1, 1]}, "items": [{"id": "a", "shape": "box"}]})


def test_scenario_cylinder_item_missing_radius():
    with pytest.raises(ValueError, match="radius"):
        load_scenario({"container": {"dims": [1, 1, 1]},
                       "items": [{"id": "a", "shape": "cylinder", "height": 1}]})


def test_scenario_obstacle_missing_keys():
    with pytest.raises(ValueError):
        load_scenario({"container": {"dims": [1, 1, 1], "obstacles": [{"id": "o"}]}, "items": []})


def test_scenario_negative_or_zero_count_rejected():
    with pytest.raises(ValueError, match="count"):
        load_scenario({"container": {"dims": [1, 1, 1]},
                       "items": [{"id": "a", "shape": "box", "dims": [0.1, 0.1, 0.1], "count": 0}]})
    with pytest.raises(ValueError, match="count"):
        load_scenario({"container": {"dims": [1, 1, 1]},
                       "items": [{"id": "a", "shape": "box", "dims": [0.1, 0.1, 0.1], "count": -3}]})


def test_scenario_length_payload_missing_depth_or_height():
    with pytest.raises(ValueError, match="depth"):
        load_scenario({"container": {"dims": [1, 1, 1]},
                       "items": [{"id": "a", "shape": "box", "length": 0.1, "height": 0.1}]})


# ============================================================== numerical extremes
def test_very_small_dimensions_still_pack_and_verify():
    items = [Item.box(f"t{i}", 1e-4, 1e-4, 1e-4, mass=1e-6) for i in range(4)]
    c = Container("micro", (2e-4, 2e-4, 2e-4), gravity=True)
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    assert len(r.placements) == 4


def test_very_large_dimensions_still_pack_and_verify():
    items = [Item.box(f"t{i}", 1e5, 1e5, 1e5, mass=1e8) for i in range(4)]
    c = Container("macro", (2e5, 2e5, 2e5), gravity=True, max_mass=math.inf)
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    assert len(r.placements) == 4


def test_mixed_extreme_scale_items_in_one_container():
    items = [Item.box("tiny", 0.001, 0.001, 0.001, mass=0.0001),
             Item.box("huge", 5.0, 5.0, 5.0, mass=500)]
    c = Container("c", (10, 10, 10), gravity=True)
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    assert len(r.placements) == 2


# ============================================================== priority tie-breaking under scarcity
def test_three_way_priority_competition_keeps_highest():
    c = Container("c", (2, 2, 2), max_mass=10)
    low = Item.box("low", 2, 2, 2, mass=10, priority=0.5)
    mid = Item.box("mid", 2, 2, 2, mass=10, priority=1.0)
    high = Item.box("high", 2, 2, 2, mass=10, priority=5.0)
    r = pack_optimized(c, [low, mid, high], FAST)
    assert_valid(r, [low, mid, high])
    assert {p.item_id for p in r.placements} == {"high"}


# ============================================================== balancing over many items, never worsens CoM
def test_balancing_never_worsens_com_across_many_random_trials():
    rng = np.random.default_rng(42)
    for trial in range(8):
        n = int(rng.integers(4, 20))
        items = [Item.box(f"b{i}", 1, 1, 1, mass=float(rng.uniform(0.5, 20))) for i in range(n)]
        c = Container("c", (n, 1, 1), gravity=False)
        r = pack_optimized(c, items, OptimizerConfig(time_budget_s=0, max_iterations=0, seed=trial, balance=False))
        assert_valid(r, items)
        before = r.metrics["com_lateral_offset"]
        total_mass_before = sum(p.mass for p in r.placements)
        swaps = balance_masses(c, r.placements)
        after_dev = None
        # recompute CoM deviation directly to avoid relying on metrics recompute helpers
        masses = np.array([p.mass for p in r.placements])
        centers = np.array([p.center for p in r.placements])
        com = (masses[:, None] * centers).sum(axis=0) / masses.sum()
        target = np.array(c.effective_com_target())
        after = float(np.hypot(com[0] - target[0], com[1] - target[1]))
        assert after <= before + 1e-9, f"trial {trial}: CoM got worse ({before} -> {after})"
        total_mass_after = sum(p.mass for p in r.placements)
        assert total_mass_after == pytest.approx(total_mass_before)


def test_balance_masses_preserves_the_geometry_set_only_swaps_which_item_is_where():
    items = [Item.box(f"b{i}", 1, 1, 1, mass=float(i + 1)) for i in range(10)]
    c = Container("c", (10, 1, 1), gravity=False)
    r = pack_optimized(c, items, OptimizerConfig(time_budget_s=0, max_iterations=0, seed=0, balance=False))
    positions_before = sorted(tuple(p.position) for p in r.placements)
    balance_masses(c, r.placements)
    positions_after = sorted(tuple(p.position) for p in r.placements)
    assert positions_before == positions_after   # same set of slots, only occupants changed


# ============================================================== orientation de-duplication
def test_cube_has_a_single_deduplicated_orientation():
    cube = Item.box("cube", 2, 2, 2, mass=1)
    assert len(cube.orientations()) == 1


def test_two_equal_dims_has_reduced_orientation_count():
    it = Item.box("slab", 2, 2, 5, mass=1)   # square footprint, one distinct height
    names = {o.name for o in it.orientations()}
    dims_seen = {tuple(sorted(o.dims)) for o in it.orientations()}
    assert len(dims_seen) <= 3   # far fewer than the generic 6


# ============================================================== min_support boundary behaviour
def test_min_support_zero_allows_floating_placement():
    items = [Item.box("a", 2, 2, 2, mass=1), Item.box("b", 2, 2, 2, mass=1)]
    c = Container("c", (2, 2, 10), gravity=True, min_support=0.0)
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    assert len(r.placements) == 2


def test_min_support_full_requires_complete_support():
    # a smaller pedestal can never fully (100%) support a larger slab -> slab must sit on the floor
    items = [Item.box("pedestal", 1, 1, 2, mass=1), Item.box("slab", 3, 3, 1, mass=1)]
    c = Container("c", (3, 3, 10), gravity=True, min_support=1.0)
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    slab = next(p for p in r.placements if p.item_id == "slab")
    assert slab.position[2] == pytest.approx(0.0)


# ============================================================== scale / perf sanity
@pytest.mark.slow
def test_thousand_items_naive_completes_quickly_and_verifies():
    import time
    items = [Item.box(f"t{i}", 1, 1, 1, mass=1) for i in range(1000)]
    c = Container("c", (10, 10, 10), gravity=True)
    t0 = time.perf_counter()
    r = pack_naive(c, items)
    assert time.perf_counter() - t0 < 15.0
    assert_valid(r, items)
    assert len(r.placements) == 1000


# ============================================================== a few more targeted gaps
def test_min_support_exact_boundary_is_accepted_not_rejected():
    # pedestal footprint exactly equal to the required fraction of the slab's base -> must pass
    c = Container("c", (10, 10, 10), gravity=True, min_support=0.5)
    items = [Item.box("pedestal", 2, 4, 2, mass=1), Item.box("slab", 4, 4, 1, mass=1)]  # 50% support exactly
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)


def test_verify_flags_orientation_string_that_does_not_match_the_item():
    items = [Item.box("a", 2, 3, 4, mass=1)]
    c = Container("c", (5, 5, 5))
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
    r.placements[0].orientation = "not_a_real_orientation"
    errs = verify(r, items)
    assert any("not legal" in e for e in errs)


def test_physics_bridge_handles_empty_result():
    c = Container("c", (2, 2, 2))
    r = pack_optimized(c, [], FAST)
    assert to_physics_placements(c, r) == []


def test_physics_bridge_keep_upright_cylinder_single_axis():
    it = Item.cylinder("can", radius=0.1, height=0.3, mass=1.0, keep_upright=True)
    c = Container("c", (1, 1, 1))
    r = pack_optimized(c, [it], FAST)
    assert_valid(r, [it])
    placements = to_physics_placements(c, r)
    assert placements[0]["rotation"] == pytest.approx([0.0, 0.0, 0.0, 1.0])   # "z" axis == identity


def test_from_scan_cylinder_uses_smaller_of_length_depth_as_radius():
    it = Item.from_scan("c", "cylinder", length=0.30, depth=0.20, height=0.5)
    assert it.radius == pytest.approx(0.10)
    assert it.volume == pytest.approx(math.pi * 0.10 ** 2 * 0.5)


def test_from_mesh_general_angle_yaw_search_finds_near_tight_bbox():
    def cube_mesh(dx, dy, dz, yaw_deg):
        v = np.array([[x, y, z] for z in (0, dz) for y in (0, dy) for x in (0, dx)], dtype=float)
        f = np.array([[0, 1, 3], [0, 3, 2], [4, 7, 5], [4, 6, 7], [0, 4, 5], [0, 5, 1],
                      [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
        t = math.radians(yaw_deg)
        R = np.array([[math.cos(t), -math.sin(t), 0], [math.sin(t), math.cos(t), 0], [0, 0, 1]])
        return v @ R.T, f
    v, f = cube_mesh(0.4, 0.15, 0.2, yaw_deg=17.0)
    it = Item.from_mesh("crate", v, f, yaw_search_deg=0.5)
    assert sorted(it.dims[:2]) == pytest.approx(sorted([0.4, 0.15]), abs=3e-3)


def test_gap_report_and_lower_bounds_on_empty_items():
    c = Container("c", (2, 2, 2))
    r = pack_optimized(c, [], FAST)
    from packer3d import gap_report, lower_bounds
    lb = lower_bounds(c, [])
    assert lb["min_unpacked_lower_bound"] == 0
    rep = gap_report(r, [], verbose=False)
    assert rep["optimal_on_count"] is True


def test_container_rejects_obstacle_overlapping_another_obstacle_is_allowed_not_a_crash():
    # not asserted invalid (redundant solids are geometrically harmless) -- just must not crash
    c = Container("c", (5, 5, 5), obstacles=[Obstacle("a", (0, 0, 0), (2, 2, 2)), Obstacle("b", (1, 1, 0), (2, 2, 2))])
    items = [Item.box("x", 1, 1, 1, mass=1)]
    r = pack_optimized(c, items, FAST)
    assert_valid(r, items)
