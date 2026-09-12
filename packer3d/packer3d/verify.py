"""Independent re-check of a packing: containment, overlap, obstacles, mass, fragile,
support and legal orientation, all computed from scratch with plain loops."""
from __future__ import annotations

import math

from .geometry import EPS, is_finite_number, rnd3
from .models import PackResult


def _overlap_len(a0, a1, b0, b1) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _all_finite(*vecs) -> bool:
    return all(is_finite_number(v) for vec in vecs for v in vec)


def verify(result: PackResult, items) -> list:
    """Return a list of human-readable violations (empty list == packing is valid)."""
    errors = []
    c = result.container
    items_by_id = {}
    for it in items:
        if it.id in items_by_id:
            errors.append(f"duplicate item id {it.id!r} in item list")
        items_by_id[it.id] = it
    L, W, H = c.dims
    R = c.dims[0] / 2.0

    # ---- bookkeeping: every item exactly once
    placed_ids = [p.item_id for p in result.placements]
    unpacked_ids = [u["id"] for u in result.unpacked]
    seen = set()
    for pid in placed_ids + unpacked_ids:
        if pid not in items_by_id:
            errors.append(f"result references unknown item {pid!r}")
        if pid in seen:
            errors.append(f"item {pid!r} appears more than once in the result")
        seen.add(pid)
    for iid in items_by_id:
        if iid not in seen:
            errors.append(f"item {iid!r} is neither placed nor reported unpacked")

    solids = []  # (lo, hi, fragile, label) for placements + obstacles
    for p in result.placements:
        it = items_by_id.get(p.item_id)
        if not _all_finite(p.position, p.dims, p.center) or not is_finite_number(p.mass):
            errors.append(f"{p.item_id}: non-finite position/dims/center/mass (NaN or inf)")
            continue
        if any(v < 0 for v in p.dims):
            errors.append(f"{p.item_id}: negative dims {p.dims}")
            continue
        lo = tuple(float(v) for v in p.position)
        d = tuple(float(v) for v in p.dims)
        hi = tuple(lo[k] + d[k] for k in range(3))
        # legal orientation for this item
        if it is not None:
            legal = [o for o in it.orientations() if o.name == p.orientation and rnd3(o.dims) == rnd3(d)]
            if not legal:
                errors.append(f"{p.item_id}: orientation {p.orientation!r} with dims {d} is not legal for this item")
            if abs(p.mass - it.mass) > EPS:
                errors.append(f"{p.item_id}: placement mass {p.mass} != item mass {it.mass}")
            if bool(p.fragile) != bool(it.fragile):
                errors.append(f"{p.item_id}: fragile flag mismatch")
        for k in range(3):
            if abs(p.center[k] - (lo[k] + d[k] / 2.0)) > EPS:
                errors.append(f"{p.item_id}: center is not the bbox centre")
                break
        # containment
        for k in range(3):
            if lo[k] < -EPS or hi[k] > c.dims[k] + EPS:
                errors.append(f"{p.item_id}: outside the container along axis {k}")
        if c.shape == "cylinder":
            for cx in (lo[0], hi[0]):
                for cy in (lo[1], hi[1]):
                    if (cx - R) ** 2 + (cy - R) ** 2 > (R + EPS) ** 2:
                        errors.append(f"{p.item_id}: footprint corner ({cx:.4f},{cy:.4f}) outside the cylinder wall")
                        break
                else:
                    continue
                break
        solids.append((lo, hi, bool(p.fragile), p.item_id))
    n_item_solids = len(solids)   # placements skipped above (non-finite/negative dims) never reach here
    for ob in c.obstacles:
        lo = tuple(float(v) for v in ob.position)
        hi = tuple(lo[k] + ob.dims[k] for k in range(3))
        solids.append((lo, hi, False, f"obstacle {ob.id}"))

    # ---- pairwise overlap (items vs items, items vs obstacles)
    n_items = n_item_solids
    for i in range(len(solids)):
        for j in range(i + 1, len(solids)):
            if i >= n_items and j >= n_items:
                continue  # obstacle-obstacle
            a, b = solids[i], solids[j]
            if all(_overlap_len(a[0][k], a[1][k], b[0][k], b[1][k]) > EPS for k in range(3)):
                errors.append(f"{a[3]} overlaps {b[3]}")

    # ---- mass
    total = sum(p.mass for p in result.placements)
    if total > c.max_mass + EPS:
        errors.append(f"total mass {total:.6g} exceeds max_mass {c.max_mass:.6g}")

    # ---- fragile: nothing rests on a fragile top face; gravity: base supported
    for i in range(n_items):
        lo, hi, _, label = solids[i]
        base = (hi[0] - lo[0]) * (hi[1] - lo[1])
        support = 0.0
        for j in range(len(solids)):
            if j == i:
                continue
            blo, bhi, bfrag, blabel = solids[j]
            if abs(bhi[2] - lo[2]) < EPS:
                area = _overlap_len(lo[0], hi[0], blo[0], bhi[0]) * _overlap_len(lo[1], hi[1], blo[1], bhi[1])
                if area > EPS:
                    support += area
                    if bfrag:
                        errors.append(f"{label} rests on fragile {blabel}")
        if base <= 0.0:   # exact zero only: base = d[0]*d[1] of literal (non-negative) dims, no cancellation
            errors.append(f"{label}: zero-area footprint ({base:.3g})")
        elif c.gravity and lo[2] > EPS and support / base < c.min_support - EPS:
            errors.append(f"{label} is not supported (support ratio {support / base:.3f} < {c.min_support})")
    return errors
