"""Provable bounds, gap reporting and exhaustive search for tiny instances.

3D bin packing is NP-hard; nothing certifies a global optimum on realistic instances.
What we *can* prove: items cannot overlap, so utilisation <= sum(volume)/usable; if the
mass limit is exceeded at least k items must stay out; items that fit in no orientation
must stay out.  ``exhaustive_small`` enumerates every sequence through the same decoder.
"""
from __future__ import annotations

import itertools
import math
import time
from typing import Optional

from .decoder import DecoderParams, decode
from .models import Container, PackResult, validate_items
from .objective import ObjectiveWeights, build_result, evaluate_state, priority_volume_total


def _min_drop(values, cap) -> int:
    """Smallest k such that the sum of all but the k largest values is <= cap."""
    if not math.isfinite(cap):
        return 0
    vals = sorted(values, reverse=True)
    total = sum(vals)
    k = 0
    while total > cap + 1e-9 and k < len(vals):
        total -= vals[k]
        k += 1
    return k


def lower_bounds(container: Container, items) -> dict:
    items = validate_items(items)
    usable = container.usable_volume
    vols = [it.volume for it in items]
    masses = [it.mass for it in items]
    total_vol = sum(vols)
    k_vol = _min_drop(vols, usable)
    k_mass = _min_drop(masses, container.max_mass)
    unfit = [it.id for it in items if not it.fits_in(container)]
    return {
        "utilization_upper_bound": min(1.0, total_vol / usable) if usable > 0 else 0.0,
        "min_unpacked_by_volume": k_vol,
        "min_unpacked_by_mass": k_mass,
        "min_unpacked_by_fit": len(unfit),
        "unfit_items": unfit,
        "min_unpacked_lower_bound": max(k_vol, k_mass, len(unfit)),
        "note": "bounds ignore gravity, fragile, upright and obstacle geometry (volume-only)",
    }


def gap_report(result: PackResult, items, verbose: bool = True) -> dict:
    lb = lower_bounds(result.container, items)
    util = result.metrics["volume_utilization"]
    n_unp = len(result.unpacked)
    rep = {
        "volume_utilization": util,
        "utilization_upper_bound": lb["utilization_upper_bound"],
        "utilization_gap": max(0.0, lb["utilization_upper_bound"] - util),
        "items_unpacked": n_unp,
        "min_unpacked_lower_bound": lb["min_unpacked_lower_bound"],
        "optimal_on_count": n_unp == lb["min_unpacked_lower_bound"],
        "bounds": lb,
    }
    if verbose:
        print(f"utilisation {util:.1%}  vs volume-only bound {lb['utilization_upper_bound']:.1%}"
              f"  (gap {rep['utilization_gap']:.1%})")
        print(f"unpacked {n_unp}  vs lower bound {lb['min_unpacked_lower_bound']}"
              f"  -> {'OPTIMAL on item count' if rep['optimal_on_count'] else 'not provably optimal on count'}")
        print(f"note: {lb['note']}")
    return rep


def exhaustive_small(container: Container, items, decoder_params: Optional[DecoderParams] = None,
                     weights: Optional[ObjectiveWeights] = None, max_items: int = 7) -> PackResult:
    """Try every item sequence (best-orientation decoding) and return the best packing."""
    items = validate_items(items)
    if len(items) > max_items:
        raise ValueError(f"exhaustive_small handles at most {max_items} items (got {len(items)})")
    weights = weights or ObjectiveWeights()
    params = decoder_params or DecoderParams()
    items_by_id = {it.id: it for it in items}
    total_pv = priority_volume_total(items)
    t0 = time.perf_counter()
    best_val, best_state, tried = math.inf, None, 0
    for seq in itertools.permutations(range(len(items))):
        st = decode(container, items, seq, None, params)
        val = evaluate_state(st, items_by_id, total_pv, weights)
        tried += 1
        if val < best_val:
            best_val, best_state = val, st
    if best_state is None:  # zero items
        best_state = decode(container, items, [], None, params)
    stats = {"sequences_tried": tried, "time_s": time.perf_counter() - t0, "objective": best_val}
    return build_result("exhaustive", container, best_state.placements, best_state.unpacked, items, weights, stats)
