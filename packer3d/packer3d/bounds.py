"""Provable bounds, gap reporting and exhaustive search for tiny instances.

3D bin packing is NP-hard; nothing certifies a global optimum on realistic instances.
What we *can* prove: two items' *solids* cannot overlap, so utilisation <=
sum(volume)/usable; if the mass limit is exceeded at least k items must stay out; items
that fit in no orientation must stay out.  ``exhaustive_small`` enumerates every sequence
through the same decoder.

"Solids", not bounding boxes, is load-bearing throughout this module.  Two bounding boxes
*can* overlap -- that is exactly what a nested placement is, a cup inside a bowl's scanned
well -- and a footprint-packed prism reserves less than its box too.  Every sum here is
over ``Item.volume`` / ``Placement.volume``, the item's true solid volume, which is why
nesting cannot double-count into these bounds.  The one place a bounding box is summed is
``gap_report``'s slack attribution, and that one has to subtract
``models.nested_overlap_by_host``.
"""
from __future__ import annotations

import itertools
import math
import time
from typing import Optional

from .decoder import DecoderParams, decode
from .models import Container, PackResult, nested_overlap_by_host, validate_items
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


def waste_bounds(container: Container, items) -> dict:
    """Provably unavoidable empty volume, and the most volume any packing could hold.

    Two items' solids cannot overlap, so no packing holds more *true* item volume than
    ``usable_volume``; and if the mass limit forces at least ``k`` of the fitting items out,
    the best case loses the ``k`` *smallest* of them.  Whatever is left over after that
    ceiling is empty space no packer could have filled -- the floor under this plan's waste.

    The proof survives nesting and footprint prisms for one reason: ``Item.volume`` is the
    true solid volume, so the inequality it rests on is "solids do not overlap" and not
    "boxes do not overlap" -- which nesting falsifies.  Stated on bounding boxes this floor
    would be wrong in the direction that matters, claiming unavoidable waste that a nest can
    in fact fill.  ``gap_report`` compares this floor against ``metrics["packed_volume"]``,
    the same true-volume basis; do not feed it a box sum.
    """
    items = validate_items(items)
    usable = container.usable_volume
    fit = [it for it in items if it.fits_in(container)]
    vols = sorted(it.volume for it in fit)
    k = _min_drop([it.mass for it in fit], container.max_mass)
    max_packable = min(usable, sum(vols) - sum(vols[:k]))
    return {
        "usable_volume": usable,
        "max_packable_volume": max(0.0, max_packable),
        "wasted_volume_lower_bound": max(0.0, usable - max_packable),
        "forced_out_by_mass": k,
        "unfittable": [it.id for it in items if not it.fits_in(container)],
    }


def gap_report(result: PackResult, items, verbose: bool = True) -> dict:
    lb = lower_bounds(result.container, items)
    wb = waste_bounds(result.container, items)
    util = result.metrics["volume_utilization"]
    n_unp = len(result.unpacked)
    by_id = {it.id: it for it in items}
    packed = result.metrics["packed_volume"]
    wasted = max(0.0, wb["usable_volume"] - packed)
    avoidable = max(0.0, wasted - wb["wasted_volume_lower_bound"])

    # who owns the gap: volume we left on the floor, then air trapped inside the bounding
    # boxes we did reserve (an irregular scan reserves far more space than it fills).
    gap_items = []
    for u in result.unpacked:
        it = by_id.get(u["id"])
        if it is not None and it.fits_in(result.container):
            gap_items.append({"id": it.id, "volume": it.volume, "kind": "left behind",
                              "detail": u.get("reason", "")})
    # A host's box reserves its cavity, and a nested guest's box then sits inside it, so the
    # guest's volume would be charged twice -- once as its own placement, once as its host's
    # slack.  Charge the host only for the air nothing fills.  (The subtraction is the guest's
    # whole box, so a guest that somehow poked into host solid would under-report slack rather
    # than invent it; the >1e-12 test already drops a negative.)
    filled = nested_overlap_by_host(result.placements)
    for p in result.placements:
        slack = p.dims[0] * p.dims[1] * p.dims[2] - p.volume - filled.get(p.item_id, 0.0)
        if slack > 1e-12:
            gap_items.append({"id": p.item_id, "volume": slack, "kind": "bbox slack",
                              "detail": "space its bounding box reserves that neither its real "
                                        "shape nor an item nested in it fills"})
    gap_items.sort(key=lambda g: -g["volume"])
    share = wb["usable_volume"] or 1.0
    for g in gap_items:
        g["share_of_usable"] = g["volume"] / share

    rep = {
        "volume_utilization": util,
        "utilization_upper_bound": lb["utilization_upper_bound"],
        "utilization_gap": max(0.0, lb["utilization_upper_bound"] - util),
        "items_unpacked": n_unp,
        "min_unpacked_lower_bound": lb["min_unpacked_lower_bound"],
        "optimal_on_count": n_unp == lb["min_unpacked_lower_bound"],
        "bounds": lb,
        # ---- how far this plan is from a provable floor on wasted volume, and who owns it
        "usable_volume": wb["usable_volume"],
        "packed_volume": packed,
        "wasted_volume": wasted,
        "wasted_volume_lower_bound": wb["wasted_volume_lower_bound"],
        "max_packable_volume": wb["max_packable_volume"],
        "avoidable_waste": avoidable,
        "avoidable_waste_fraction": avoidable / share,
        "provably_tight_on_volume": avoidable <= 1e-9,
        "gap_items": gap_items,
        "waste_note": "waste floor assumes perfect nesting; it ignores gravity, fragile, "
                      "upright and obstacle geometry, so a real packer cannot always reach it",
    }
    if verbose:
        print(f"utilisation {util:.1%}  vs volume-only bound {lb['utilization_upper_bound']:.1%}"
              f"  (gap {rep['utilization_gap']:.1%})")
        print(f"unpacked {n_unp}  vs lower bound {lb['min_unpacked_lower_bound']}"
              f"  -> {'OPTIMAL on item count' if rep['optimal_on_count'] else 'not provably optimal on count'}")
        print(f"wasted volume {wasted:.4g} of {wb['usable_volume']:.4g} usable; provable floor "
              f"{wb['wasted_volume_lower_bound']:.4g}  -> {avoidable:.4g} "
              f"({rep['avoidable_waste_fraction']:.1%} of the container) is avoidable"
              + ("  [TIGHT: no packing wastes less]" if rep["provably_tight_on_volume"] else ""))
        for g in gap_items[:5]:
            print(f"    {g['id']:<14} {g['volume']:.4g} ({g['share_of_usable']:.1%} of usable)"
                  f"  {g['kind']}" + (f": {g['detail']}" if g["detail"] else ""))
        if not gap_items:
            print("    (no item accounts for the gap: every item that fits is packed)")
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
