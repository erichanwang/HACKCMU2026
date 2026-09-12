"""Seeded fuzz test: random scenarios into pack_naive / pack_optimized, checked with verify()
plus a few invariants verify() doesn't cover (upright axis, compression, JSON cleanliness).

Kept cheap on purpose (no multi-start, 1 SA iteration) -- this is a property fuzz, not a
quality benchmark; see test_edge_cases.py for the real quality/behaviour assertions.
"""
import json
import math
import random

import pytest

from packer3d import Container, Item, OptimizerConfig, load_scenario, pack_naive, pack_optimized, verify

N_TRIALS = 30          # "~50" scenarios incl. the scan-form trials below, budget-limited
N_SCAN_TRIALS = 8
CHEAP = dict(time_budget_s=0, max_iterations=1, multi_start=False)


def _assert_no_numpy(obj):
    """Only plain JSON-native types after json.loads -- a stray numpy scalar has type()
    numpy.float64/int64 etc, which is *not* in this tuple even though np.float64 isinstance
    of float."""
    allowed = (dict, list, str, int, float, bool, type(None))
    assert type(obj) in allowed, f"non-plain type in JSON output: {type(obj)}"
    if isinstance(obj, dict):
        for v in obj.values():
            _assert_no_numpy(v)
    elif isinstance(obj, list):
        for v in obj:
            _assert_no_numpy(v)


def check_invariants(r, items):
    errs = verify(r, items)
    assert errs == [], errs

    items_by_id = {it.id: it for it in items}
    ids_in = set(items_by_id)
    ids_out = {p.item_id for p in r.placements} | {u["id"] for u in r.unpacked}
    assert ids_in == ids_out          # every item placed or unpacked exactly once

    for p in r.placements:            # upright items keep the up axis (verify() also enforces
        it = items_by_id[p.item_id]   # legal orientation; this pins down *which* one)
        if not it.keep_upright:
            continue
        if p.shape == "cylinder":
            assert p.axis == "z"
        else:
            assert p.orientation in ("xyz", "yxz")

    for k, v in r.metrics.items():
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)):
            assert math.isfinite(v), f"metrics[{k}] = {v}"
        elif isinstance(v, (list, tuple)):
            assert all(math.isfinite(x) for x in v if isinstance(x, (int, float))), f"metrics[{k}] = {v}"
    assert r.metrics["com_lateral_offset"] >= 0.0

    d = json.loads(r.to_json())       # round-trips and is JSON-clean
    _assert_no_numpy(d)


def random_item(rng, iid, container_dims, oversize_axis=None):
    shape = rng.choice(["box", "cylinder"])
    fragile = rng.random() < 0.25
    keep_upright = rng.random() < 0.25
    mass = round(rng.uniform(0.0, 5.0), 3)
    k = round(rng.uniform(1.2, 3.0), 2) if rng.random() < 0.2 else 1.0

    if shape == "box":
        dims = [max(0.02, rng.uniform(0.05, 0.9) * container_dims[a]) for a in range(3)]
        if oversize_axis is not None:
            dims[oversize_axis] = container_dims[oversize_axis] * rng.uniform(1.2, 1.6)
        it = Item.box(iid, dims[0], dims[1], dims[2], mass, fragile=fragile, keep_upright=keep_upright)
        orig_h = it.dims[2]
    else:
        radius = max(0.01, rng.uniform(0.02, 0.4) * min(container_dims[0], container_dims[1]))
        height = max(0.02, rng.uniform(0.05, 0.9) * container_dims[2])
        if oversize_axis == 2:
            height = container_dims[2] * rng.uniform(1.2, 1.6)
        elif oversize_axis is not None:
            radius = min(container_dims[0], container_dims[1]) * rng.uniform(0.7, 1.2)
        it = Item.cylinder(iid, radius, height, mass, fragile=fragile, keep_upright=keep_upright)
        orig_h = it.height

    if k > 1.0:
        compressed = it.compressed(k)
        assert compressed.dims[2] == pytest.approx(orig_h / k)
        it = compressed
    return it


def test_fuzz_random_scenarios():
    for trial in range(N_TRIALS):
        rng = random.Random(1000 + trial)
        container_dims = tuple(round(rng.uniform(0.5, 2.5), 3) for _ in range(3))
        container = Container(f"c{trial}", container_dims)
        n_items = rng.randint(1, 12)
        oversize_idx = rng.randrange(n_items) if rng.random() < 0.3 else -1
        items = [
            random_item(rng, f"s{trial}_i{k}", container_dims,
                        oversize_axis=rng.randrange(3) if k == oversize_idx else None)
            for k in range(n_items)
        ]

        r_naive = pack_naive(container, items)
        check_invariants(r_naive, items)

        if trial % 2 == 0:   # both functions get fuzzed; halved to stay inside the CPU budget
            r_opt = pack_optimized(container, items, OptimizerConfig(seed=trial, **CHEAP))
            check_invariants(r_opt, items)


def random_heightmap_dict(rng, iid):
    cell = 0.02
    rows, cols = rng.randint(2, 4), rng.randint(2, 4)
    width, depth = rows * cell, cols * cell
    height = round(rng.uniform(0.05, 0.3), 3)
    heights = [[round(rng.uniform(0, height), 4) if rng.random() > 0.15 else 0.0 for _ in range(cols)]
               for _ in range(rows)]
    rigidity = rng.choice(["rigid", "soft", "fragile"])
    d = {"id": iid, "dimensions": [width, height, depth], "cellSize": cell, "heights": heights,
        "mass": round(rng.uniform(0.0, 3.0), 2), "keepUpright": rng.random() < 0.3, "rigidity": rigidity}
    if rigidity == "soft":
        d["compressibility"] = round(rng.uniform(1.0, 3.0), 2)
    return d


def test_fuzz_scan_document_scenarios():
    """Server-document scan form (SCAN_OUTPUT.md): metres, `dimensions`, `heights` grid."""
    for trial in range(N_SCAN_TRIALS):
        rng = random.Random(9000 + trial)
        n_items = rng.randint(1, 5)
        container_dims = [round(rng.uniform(0.3, 1.5), 3) for _ in range(3)]
        scenario = {"container": {"dims": container_dims},
                   "items": [random_heightmap_dict(rng, f"scan{trial}_{k}") for k in range(n_items)]}
        container, items, _config, _weights = load_scenario(scenario)

        r_naive = pack_naive(container, items)
        check_invariants(r_naive, items)

        if trial % 2 == 0:
            r_opt = pack_optimized(container, items, OptimizerConfig(seed=trial, **CHEAP))
            check_invariants(r_opt, items)
