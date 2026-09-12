"""Mass-swap balancing: exact, geometry-preserving centre-of-mass improvement.

Interchangeable items -- same oriented bounding box, same fragile flag, and the same *real*
shape (see ``_shape_key``) -- swap *positions* (never orientations).  The geometry of the
packing is unchanged, so every constraint still holds without re-checking; only the mass
distribution moves.  The moment update for swapping i and j is  delta = (m_i - m_j) *
(c_j - c_i), evaluated in O(1).
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from .geometry import EPS, rnd3
from .models import Container


def _shape_key(p):
    """The part of the group key that stands for the item's real shape, not just its bbox.

    A scanned item is not a solid box: it carries a ``height_grid`` and decomposes into
    cavity solids (``Item.solid_boxes``), so two placements with the same oriented bounding
    box can be a bowl and a solid block.  Exchanging those moves a cavity to where there is
    none -- an item the decoder nested in the bowl's interior ends up inside a solid block,
    and its ``nested_in`` cavity now describes a host that has moved away.

    A ``Placement`` carries no shape detail (no grid, no solid boxes), so the only sound key
    for a placement that might have one is a unique key: a box-shaped item that came from a
    scan never swaps.  ``scan_shape`` is the field that means "measured, not a primitive",
    and it is set for every heightmap scan.  Plain boxes and cylinders -- no grid geometry,
    ``oriented_solid_boxes`` gives them their bounding box -- key exactly as they did before.

    ponytail: an exact key (the grid's contents, which are already a hashable tuple) needs
    the ``Item``s, which only the caller has; ``search.py`` would have to pass them in.  On
    the live path that buys nothing: a group needs equal dims *and* unequal masses, and two
    scans of two objects never produce bit-identical grids anyway.
    """
    return p.item_id if p.shape == "box" and p.scan_shape is not None else None


def balance_masses(container: Container, placements, max_rounds: int = 10_000) -> int:
    """Greedy best-improvement swaps; mutates ``placements`` in place. Returns #swaps."""
    n = len(placements)
    if n < 2:
        return 0
    dims = np.asarray(container.dims, dtype=float)
    target = np.asarray(container.effective_com_target(), dtype=float)
    axis_w = np.asarray(container.effective_com_axis_weights(), dtype=float)
    masses = np.array([p.mass for p in placements], dtype=float)
    centers = np.array([p.center for p in placements], dtype=float)
    M = float(masses.sum())
    if M <= EPS:
        return 0
    moment = (masses[:, None] * centers).sum(axis=0)

    groups = defaultdict(list)
    for i, p in enumerate(placements):
        groups[(rnd3(p.dims), bool(p.fragile), _shape_key(p))].append(i)
    groups = [np.array(g) for g in groups.values() if len(g) >= 2 and len(set(masses[g].round(12))) > 1]
    if not groups:
        return 0

    def dev_of(mom):
        d = (mom / M - target) / dims
        return np.sqrt(np.sum(axis_w * d * d, axis=-1))

    cur = float(dev_of(moment))
    swaps = 0
    for _ in range(max_rounds):
        best_gain, best_pair = 1e-12, None
        for g in groups:
            m, c = masses[g], centers[g]
            dm = m[:, None] - m[None, :]                       # (g,g)
            dc = c[None, :, :] - c[:, None, :]                 # (g,g,3): c_j - c_i
            new_mom = moment[None, None, :] + dm[:, :, None] * dc
            gain = cur - dev_of(new_mom)                       # (g,g)
            gain = np.triu(gain, k=1)
            k = int(np.argmax(gain))
            i, j = divmod(k, len(g))
            if gain[i, j] > best_gain:
                best_gain, best_pair = float(gain[i, j]), (int(g[i]), int(g[j]))
        if best_pair is None:
            break
        i, j = best_pair
        moment = moment + (masses[i] - masses[j]) * (centers[j] - centers[i])
        centers[[i, j]] = centers[[j, i]]
        pi, pj = placements[i], placements[j]
        pi.position, pj.position = pj.position, pi.position
        pi.center, pj.center = pj.center, pi.center
        pi.nested_in, pj.nested_in = pj.nested_in, pi.nested_in   # describes the slot, not the item
        cur = float(dev_of(moment))
        swaps += 1
    return swaps
