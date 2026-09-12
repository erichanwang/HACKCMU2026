"""Independent re-check of a packing: containment, overlap, obstacles, mass, fragile,
support, keep-upright and legal orientation, all computed from scratch with plain loops.

Every message names the items involved and by how much they are wrong: these strings are
what a human reads when four candidate packings are ranked and one of them is bad, and a
bare "invalid" tells them nothing.

Nesting: a placement is decomposed with ``oriented_solid_boxes`` into the same
heightmap-derived sub-boxes the decoder placed against, so two bounding boxes that
interpenetrate are legal exactly when one item really sits in the other's scanned cavity
and are a collision otherwise.  That symmetry with the decoder is load-bearing -- verify
re-derives every collision independently, but from the *same* notion of where an item is
solid, or every correct nested placement would be reported as an overlap.  An item with no
height grid decomposes to its plain bounding box: no scan, no cavity, no excuse.
"""
from __future__ import annotations

import math

from .geometry import EPS, is_finite_number, rnd3
from .models import BOX_ORIENTATIONS, PackResult, oriented_solid_boxes

# Orientations that leave the item's own z pointing up; anything else is a tipped item.
UPRIGHT_ORIENTATIONS = frozenset(
    [name for name, perm in BOX_ORIENTATIONS.items() if perm[2] == 2] + ["cyl_axis_z"])

OBSTACLE = -1   # owner id shared by every obstacle sub-box


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

    solids = []  # (lo, hi, fragile, label, owner) for placement sub-boxes + obstacles
    for owner, p in enumerate(result.placements):
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
            if it.keep_upright and p.orientation not in UPRIGHT_ORIENTATIONS:
                errors.append(f"{p.item_id}: keep-upright item was tipped onto its side "
                              f"(orientation {p.orientation!r}, dims {d}; upright orientations "
                              f"are {sorted(UPRIGHT_ORIENTATIONS)})")
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
                errors.append(f"{p.item_id}: outside the container {c.id!r} along axis {k} "
                              f"-- spans {lo[k]:.4g}..{hi[k]:.4g}, container is 0..{c.dims[k]:.4g}")
        if c.shape == "cylinder":
            for cx in (lo[0], hi[0]):
                for cy in (lo[1], hi[1]):
                    if (cx - R) ** 2 + (cy - R) ** 2 > (R + EPS) ** 2:
                        errors.append(f"{p.item_id}: footprint corner ({cx:.4f},{cy:.4f}) outside the cylinder wall")
                        break
                else:
                    continue
                break
        # decompose into the same heightmap-derived sub-boxes the decoder actually placed
        # against, instead of one solid bbox -- otherwise a real nested placement (a cup
        # sitting in a bowl's true-shape cavity) would misreport as an overlap here.
        sub_boxes = oriented_solid_boxes(it, lo, d, p.orientation) if it is not None else [(lo, hi)]
        for wlo, whi in sub_boxes:
            solids.append((wlo, whi, bool(p.fragile), p.item_id, owner))
    n_item_solids = len(solids)   # placements skipped above (non-finite/negative dims) never reach here
    for ob in c.obstacles:
        lo = tuple(float(v) for v in ob.position)
        hi = tuple(lo[k] + ob.dims[k] for k in range(3))
        solids.append((lo, hi, False, f"obstacle {ob.id}", OBSTACLE))

    # ---- pairwise overlap (items vs items, items vs obstacles).  Reported once per pair
    # of items, at the deepest sub-box clash, so one bad placement is one message.
    clashes = {}
    for i in range(len(solids)):
        for j in range(i + 1, len(solids)):
            a, b = solids[i], solids[j]
            if a[4] == b[4]:
                continue  # two sub-boxes of one placement, or obstacle vs obstacle
            ov = [_overlap_len(a[0][k], a[1][k], b[0][k], b[1][k]) for k in range(3)]
            depth = min(ov)
            if depth <= EPS:
                continue
            key = (a[3], b[3])
            if key not in clashes or depth > clashes[key][0]:
                clashes[key] = (depth, "xyz"[ov.index(depth)])
    for (la, lb), (depth, axis) in clashes.items():
        errors.append(f"{la} overlaps {lb} by {depth:.4g} m along {axis} -- their solid parts "
                      f"intersect, so this is a collision and not a nest")

    # ---- mass
    total = sum(p.mass for p in result.placements)
    if total > c.max_mass + EPS:
        heavy = ", ".join(f"{p.item_id}={p.mass:.6g}"
                          for p in sorted(result.placements, key=lambda q: -q.mass)[:2])
        errors.append(f"total mass {total:.6g} exceeds max_mass {c.max_mass:.6g} of container "
                      f"{c.id!r} by {total - c.max_mass:.6g} (heaviest placed: {heavy})")

    # ---- fragile: nothing rests on a fragile top face; gravity: base supported
    on_fragile = {}   # (item, fragile item) -> [contact area, contact z]
    unsupported = {}  # item -> (support ratio, message), worst sub-box wins
    for i in range(n_item_solids):
        lo, hi, _frag, label, owner = solids[i]
        base = (hi[0] - lo[0]) * (hi[1] - lo[1])
        support = 0.0
        below = None   # (top z, label) of the nearest solid anywhere under this one
        for j in range(len(solids)):
            blo, bhi, bfrag, blabel, bowner = solids[j]
            if bowner == owner:
                continue
            area = _overlap_len(lo[0], hi[0], blo[0], bhi[0]) * _overlap_len(lo[1], hi[1], blo[1], bhi[1])
            if area <= EPS:
                continue
            if bhi[2] <= lo[2] + EPS and (below is None or bhi[2] > below[0]):
                below = (bhi[2], blabel)
            if abs(bhi[2] - lo[2]) < EPS:
                support += area
                if bfrag:
                    hit = on_fragile.setdefault((label, blabel), [0.0, lo[2]])
                    hit[0] += area
        if base <= 0.0:   # exact zero only: base = d[0]*d[1] of literal (non-negative) dims, no cancellation
            errors.append(f"{label}: zero-area footprint ({base:.3g})")
        elif c.gravity and lo[2] > EPS and support / base < c.min_support - EPS:
            ratio = support / base
            under = ("nothing is under it" if below is None else
                     f"nearest solid below is {below[1]}, top at z={below[0]:.4g} "
                     f"({lo[2] - below[0]:.4g} m gap)")
            if label not in unsupported or ratio < unsupported[label][0]:
                unsupported[label] = (ratio, f"{label} is not supported: base at z={lo[2]:.4g} has "
                                             f"support ratio {ratio:.3f} < {c.min_support} -- {under}")
    for (la, lb), (area, z) in on_fragile.items():
        errors.append(f"{la} rests on fragile {lb} (contact area {area:.4g} m^2 at z={z:.4g})")
    for _label, (_ratio, msg) in unsupported.items():
        errors.append(msg)
    return errors
