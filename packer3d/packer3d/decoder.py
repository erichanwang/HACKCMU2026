"""Constructive decoder: extreme points + vectorised feasibility + best-fit scoring.

Given a sequence of items (and optionally an orientation per item) the decoder places
them one at a time.  Every placement it commits satisfies containment, no-overlap,
obstacle, fragile and gravity-support constraints, so the packing is feasible by
construction; ``verify()`` re-checks independently.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Optional

import numpy as np

from .geometry import EPS, rnd3
from .models import Container, Item, Placement, oriented_solid_boxes

REASON_TOO_BIG = "larger than the container in every allowed orientation"
REASON_MASS = "would exceed the container mass limit"
REASON_NO_SPACE = "no feasible position (space, support or fragile constraints)"
OBSTACLE = -1   # solid owner id shared by every container obstacle


@dataclass
class DecoderParams:
    """Placement heuristic weights.

    score = w_z*z/H + w_y*y/W + w_x*x/L - w_contact*(touching area / item surface)
            + w_com*(CoM deviation after placing)          (lower is better)
    """
    w_z: float = 1.0
    w_y: float = 0.3
    w_x: float = 0.1
    w_contact: float = 0.5
    w_com: float = 1.0
    grid_max_per_axis: int = 12   # fallback grid density (per axis, plus solid boundaries)
    chunk: int = 1024             # candidates evaluated per vectorised batch


NAIVE_PARAMS = DecoderParams(w_z=1e4, w_y=1e2, w_x=1.0, w_contact=0.0, w_com=0.0)


class PackState:
    """Incremental packing state: solids (obstacles + placed items), extreme points, mass moments."""

    def __init__(self, container: Container, params: Optional[DecoderParams] = None):
        self.c = container
        self.p = params or DecoderParams()
        self.dims = np.asarray(container.dims, dtype=float)
        self.is_cyl = container.shape == "cylinder"
        self.R = container.dims[0] / 2.0
        self.gravity = container.gravity
        self.min_support = container.min_support
        self.target = np.asarray(container.effective_com_target(), dtype=float)
        self.axis_w = np.asarray(container.effective_com_axis_weights(), dtype=float)

        self.smin = np.zeros((0, 3))
        self.smax = np.zeros((0, 3))
        self.sfrag = np.zeros(0, dtype=bool)
        self.sowner = np.zeros(0, dtype=int)   # index into self.placements, OBSTACLE for obstacles
        self.n_obstacles = 0

        self.placements: list[Placement] = []
        self.unpacked: list[dict] = []
        self.chosen_orient: dict[str, int] = {}
        self.total_mass = 0.0
        self.mass_moment = np.zeros(3)
        self.total_bbox_vol = 0.0
        self.total_occupied_vol = 0.0  # sum of solid_boxes() volumes -- <= total_bbox_vol when items have cavities
        self.vol_moment = np.zeros(3)
        self.total_true_vol = 0.0

        self._hosts: list[int] = []   # placements whose solids do not fill their bbox: can host a nest
        self._eps: dict[tuple, None] = {}
        self._ep_cache: Optional[np.ndarray] = None
        self._fail_cache: dict[tuple, int] = {}

        self._add_ep(np.zeros(3))
        if self.is_cyl:  # seed inside the inscribed square (origin is outside the circle)
            s = self.R * (1.0 - 1.0 / math.sqrt(2.0))
            self._add_ep(np.array([s, s, 0.0]))
        for ob in container.obstacles:
            lo = np.asarray(ob.position, dtype=float)
            self._add_solid(lo, lo + np.asarray(ob.dims, dtype=float), fragile=False)
            self.n_obstacles += 1

    # ------------------------------------------------------------------ CoM
    def com(self) -> np.ndarray:
        if self.total_mass > EPS:
            return self.mass_moment / self.total_mass
        if self.total_bbox_vol > 0:
            return self.vol_moment / self.total_bbox_vol
        return self.target.copy()

    def com_deviation(self) -> float:
        d = (self.com() - self.target) / self.dims
        return float(np.sqrt(np.sum(self.axis_w * d * d)))

    # ------------------------------------------------------------------ EPs
    def _add_ep(self, p) -> None:
        key = rnd3(p)
        if key not in self._eps:
            self._eps[key] = None
            self._ep_cache = None

    def _ep_array(self) -> np.ndarray:
        if self._ep_cache is None:
            self._ep_cache = np.array(list(self._eps.keys()), dtype=float).reshape(-1, 3)
        return self._ep_cache

    def _point_inside_container(self, p) -> bool:
        if np.any(p < -EPS) or np.any(p > self.dims + EPS):
            return False
        if self.is_cyl:
            return (p[0] - self.R) ** 2 + (p[1] - self.R) ** 2 <= (self.R + EPS) ** 2
        return True

    def _point_in_solid(self, p) -> bool:
        if len(self.smin) == 0:
            return False
        return bool(np.any(np.all((p > self.smin + EPS) & (p < self.smax - EPS), axis=1)))

    def _wall(self, a: int, p) -> float:
        """Coordinate of the container wall when projecting point p along -axis a."""
        if a == 2 or not self.is_cyl:
            return 0.0
        b = 1 - a
        off = p[b] - self.R
        return self.R - math.sqrt(max(0.0, self.R * self.R - off * off))

    def _project(self, p, a: int) -> float:
        b, c = [k for k in range(3) if k != a]
        base = self._wall(a, p)
        if len(self.smin) == 0:
            return base
        m = ((self.smax[:, a] <= p[a] + EPS)
             & (self.smin[:, b] - EPS <= p[b]) & (p[b] < self.smax[:, b] - EPS)
             & (self.smin[:, c] - EPS <= p[c]) & (p[c] < self.smax[:, c] - EPS))
        if m.any():
            return max(base, float(self.smax[m, a].max()))
        return base

    def _add_solid(self, lo, hi, fragile: bool, owner: int = OBSTACLE) -> None:
        lo = np.round(np.asarray(lo, dtype=float), 9)
        hi = np.round(np.asarray(hi, dtype=float), 9)
        self.smin = np.vstack([self.smin, lo[None, :]])
        self.smax = np.vstack([self.smax, hi[None, :]])
        self.sfrag = np.append(self.sfrag, bool(fragile))
        self.sowner = np.append(self.sowner, int(owner))
        # extreme points strictly inside the new solid are dead
        if self._eps:
            arr = self._ep_array()
            inside = np.all((arr > lo + EPS) & (arr < hi - EPS), axis=1)
            if inside.any():
                keys = list(self._eps.keys())
                for i in np.nonzero(inside)[0]:
                    del self._eps[keys[i]]
                self._ep_cache = None
        # new extreme points: three outer corners, each projected along the other two axes
        corners = (
            (0, np.array([hi[0], lo[1], lo[2]])),
            (1, np.array([lo[0], hi[1], lo[2]])),
            (2, np.array([lo[0], lo[1], hi[2]])),
        )
        for moved, p in corners:
            cands = [p]
            for a in range(3):
                if a == moved:
                    continue
                q = p.copy()
                q[a] = self._project(p, a)
                cands.append(q)
            for q in cands:
                if self._point_inside_container(q) and not self._point_in_solid(q):
                    self._add_ep(q)

    # ------------------------------------------------------------------ grid fallback
    def _grid(self, d) -> np.ndarray:
        d = np.asarray(d, dtype=float)
        n = max(2, int(self.p.grid_max_per_axis))
        axes = []
        for a in range(3):
            top = self.dims[a] - d[a]
            if a == 2 and self.gravity:  # only floor / solid tops can support an item
                zs = {0.0}
                if len(self.smax):
                    zs.update(np.round(self.smax[:, 2], 9).tolist())
                axes.append(np.array(sorted(z for z in zs if z <= top + EPS), dtype=float))
                continue
            vals = {0.0, float(max(0.0, top))}
            if len(self.smin):
                vals.update(np.round(self.smin[:, a], 9).tolist())
                vals.update(np.round(self.smax[:, a], 9).tolist())
                vals.update(np.round(self.smin[:, a] - d[a], 9).tolist())
            if self.is_cyl and a < 2:
                vals.add(float(np.round(self.R - d[a] / 2.0, 9)))
            vals = sorted(v for v in vals if -EPS <= v <= top + EPS)
            if len(vals) > n:
                idx = np.unique(np.linspace(0, len(vals) - 1, n).round().astype(int))
                vals = [vals[i] for i in idx]
            even = np.round(np.linspace(0.0, max(0.0, top), n), 9).tolist()
            axes.append(np.array(sorted(set(vals) | set(even)), dtype=float))
        xs, ys, zs = axes
        if len(zs) == 0:
            return np.zeros((0, 3))
        if self.is_cyl:
            pairs = [(x, y) for x in xs for y in ys]
            R = self.R
            for y in ys:  # tightest x positions against the curved wall for this y-row
                half = max(abs(y - R), abs(y + d[1] - R))
                if half <= R:
                    w = math.sqrt(R * R - half * half)
                    x_lo, x_hi = R - w, R + w - d[0]
                    if x_lo <= x_hi + EPS:
                        pairs.append((x_lo, y))
                        pairs.append((max(x_lo, x_hi), y))
            pairs = np.array(pairs, dtype=float).reshape(-1, 2)
        else:
            pairs = np.array([(x, y) for x in xs for y in ys], dtype=float).reshape(-1, 2)
        cands = np.concatenate(
            [np.column_stack([pairs, np.full(len(pairs), z)]) for z in zs], axis=0)
        return np.round(cands, 9)

    # ------------------------------------------------------------------ feasibility + score
    def _feasible_and_score(self, lo, d, m, bbox_vol, pos, fragile=False, round_xy=False):
        """Vectorised feasibility and placement score for candidate min-corners ``lo`` (C,3)."""
        C = len(lo)
        hi = lo + d
        L, W, H = self.dims
        ok = np.all(lo >= -EPS, axis=1) & np.all(hi <= self.dims + EPS, axis=1)
        if self.is_cyl:
            R = self.R
            if round_xy:
                # An upright cylinder sweeps a circle, not its bounding square: it clears the
                # bore whenever its own axis is within R - r of the container's.  Testing its
                # bbox corners instead would reject anything wider than R * sqrt(2).
                r = d[0] / 2.0
                ok &= np.hypot(lo[:, 0] + r - R, lo[:, 1] + r - R) <= R - r + EPS
            else:
                lim = (R + EPS) ** 2
                for cx in (lo[:, 0], hi[:, 0]):
                    for cy in (lo[:, 1], hi[:, 1]):
                        ok &= (cx - R) ** 2 + (cy - R) ** 2 <= lim
        surf = 2.0 * (d[0] * d[1] + d[1] * d[2] + d[0] * d[2])
        base_area = d[0] * d[1]
        touch = np.zeros(C)
        touch += ((lo[:, 2] < EPS) | (np.abs(hi[:, 2] - H) < EPS)) * base_area
        if not self.is_cyl:
            touch += ((lo[:, 0] < EPS) | (np.abs(hi[:, 0] - L) < EPS)) * (d[1] * d[2])
            touch += ((lo[:, 1] < EPS) | (np.abs(hi[:, 1] - W) < EPS)) * (d[0] * d[2])
        support = np.zeros(C)
        if len(self.smin):
            smin = self.smin[None, :, :]
            smax = self.smax[None, :, :]
            l3 = lo[:, None, :]
            h3 = hi[:, None, :]
            ov = np.clip(np.minimum(h3, smax) - np.maximum(l3, smin), 0.0, None)  # (C,S,3)
            ok &= ~np.any(np.all(ov > EPS, axis=2), axis=1)                        # no overlap
            axy = ov[:, :, 0] * ov[:, :, 1]
            ayz = ov[:, :, 1] * ov[:, :, 2]
            axz = ov[:, :, 0] * ov[:, :, 2]
            below = np.abs(smax[:, :, 2] - l3[:, :, 2]) < EPS       # solid top flush with base
            contact_below = axy * below
            ok &= ~np.any((contact_below > EPS) & self.sfrag[None, :], axis=1)     # fragile below
            sb = contact_below.sum(axis=1)
            support = sb / base_area
            touch += sb
            contact_above = axy * (np.abs(smin[:, :, 2] - h3[:, :, 2]) < EPS)  # solid base flush with top
            if fragile:  # nothing may already rest on a fragile item's top face
                ok &= ~np.any(contact_above > EPS, axis=1)
            touch += contact_above.sum(axis=1)
            touch += (ayz * ((np.abs(smax[:, :, 0] - l3[:, :, 0]) < EPS)
                             | (np.abs(smin[:, :, 0] - h3[:, :, 0]) < EPS))).sum(axis=1)
            touch += (axz * ((np.abs(smax[:, :, 1] - l3[:, :, 1]) < EPS)
                             | (np.abs(smin[:, :, 1] - h3[:, :, 1]) < EPS))).sum(axis=1)
        if self.gravity:
            ok &= (lo[:, 2] < EPS) | (support >= self.min_support - EPS)
        center = lo + d / 2.0
        if self.total_mass + m > EPS:
            com_after = (self.mass_moment + m * center) / (self.total_mass + m)
        else:
            com_after = (self.vol_moment + bbox_vol * center) / (self.total_bbox_vol + bbox_vol)
        dv = (com_after - self.target) / self.dims
        dev = np.sqrt(np.sum(self.axis_w * dv * dv, axis=1))
        score = pos - self.p.w_contact * (touch / surf) + self.p.w_com * dev
        return ok, score

    def _best_candidate(self, cands, d, m, bbox_vol, best=math.inf, fragile=False, round_xy=False):
        """Best feasible candidate with score < ``best`` (branch-and-bound over sorted chunks)."""
        if len(cands) == 0:
            return best, None
        keep = np.all(cands + d <= self.dims + EPS, axis=1) & np.all(cands >= -EPS, axis=1)
        if self.gravity:
            tops = np.unique(np.round(self.smax[:, 2], 9)) if len(self.smax) else np.zeros(0)
            keep &= (cands[:, 2] < EPS) | np.isin(np.round(cands[:, 2], 9), tops)
        cands = cands[keep]
        if len(cands) == 0:
            return best, None
        L, W, H = self.dims
        pos = self.p.w_z * cands[:, 2] / H + self.p.w_y * cands[:, 1] / W + self.p.w_x * cands[:, 0] / L
        lb = pos - self.p.w_contact
        order = np.argsort(lb, kind="stable")
        chunk = max(16, int(self.p.chunk))
        best_pos = None
        for start in range(0, len(order), chunk):
            idx = order[start:start + chunk]
            if lb[idx[0]] >= best:
                break
            ok, score = self._feasible_and_score(cands[idx], d, m, bbox_vol, pos[idx], fragile, round_xy)
            if ok.any():
                sc = np.where(ok, score, np.inf)
                j = int(np.argmin(sc))
                if sc[j] < best:
                    best = float(sc[j])
                    best_pos = cands[idx[j]]
        return best, best_pos

    # ------------------------------------------------------------------ placing
    def place(self, item: Item, orient_idx: Optional[int] = None) -> bool:
        """Place ``item``; ``orient_idx`` = preferred orientation (others tried if it fails)
        or None = evaluate every legal orientation and keep the best-scoring one."""
        oris = item.orientations()
        if not item.fits_in(self.c):
            self.unpacked.append({"id": item.id, "reason": REASON_TOO_BIG})
            return False
        if self.total_mass + item.mass > self.c.max_mass + EPS:
            self.unpacked.append({"id": item.id, "reason": REASON_MASS})
            return False
        bbox_vol = item.bbox_volume
        if self.c.usable_volume - self.total_occupied_vol < item.occupied_volume - EPS:
            self.unpacked.append({"id": item.id, "reason": REASON_NO_SPACE})
            return False
        if orient_idx is None:
            order = list(range(len(oris)))
        else:
            k0 = int(orient_idx) % len(oris)
            order = [k0] + [k for k in range(len(oris)) if k != k0]
        n_solids = len(self.smin)
        keys = {k: (rnd3(oris[k].dims), item.fragile) for k in order}
        order = [k for k in order if self._fail_cache.get(keys[k]) != n_solids]
        best_score, best_pos, best_k = math.inf, None, None
        for stage in ("ep", "grid"):
            for k in order:
                d = np.asarray(oris[k].dims, dtype=float)
                cands = self._ep_array() if stage == "ep" else self._grid(d)
                sc, pos = self._best_candidate(cands, d, item.mass, bbox_vol, best_score, item.fragile,
                                               oris[k].axis == "z")
                if pos is not None:
                    best_score, best_pos, best_k = sc, pos, k
                    if orient_idx is not None:   # genome mode: first orientation that works
                        break
            if best_pos is not None:
                break
        if best_pos is None:
            for k in order:
                self._fail_cache[keys[k]] = n_solids
            self.unpacked.append({"id": item.id, "reason": REASON_NO_SPACE})
            return False
        self._commit(item, oris[best_k], best_k, best_pos)
        return True

    def _nested_in(self, lo, hi) -> Optional[dict]:
        """``nested_in`` for a placement about to be committed at ``lo``..``hi``, or None.

        Called only from ``_commit``, i.e. only for a candidate ``_feasible_and_score`` already
        proved clear of every *solid* sub-box in the state.  So if its bounding box lands inside
        an already-placed item's bounding box, that item has a scanned cavity and this really is
        a nest -- which is why the fact is recorded here, by the code that did it, and never
        re-derived downstream from "these two boxes intersect" (that would relabel a genuine
        collision as a nest).  The cavity reported is the host's free space under this item:
        its own footprint clipped to the host, from the top of the host's solids there up to
        the host's lid -- a region no host solid can intersect, by construction, and not the
        host's bounding box (a host with several cavity cells answers "may these two overlap"
        differently in each).
        """
        host_i, host, best_vol = None, None, 0.0
        for i in self._hosts:
            q = self.placements[i]
            qlo = np.asarray(q.position, dtype=float)
            ov = np.minimum(hi, qlo + np.asarray(q.dims, dtype=float)) - np.maximum(lo, qlo)
            if np.all(ov > EPS) and float(np.prod(ov)) > best_vol:
                host_i, host, best_vol = i, q, float(np.prod(ov))
        if host is None:
            return None
        hlo = np.asarray(host.position, dtype=float)
        hhi = hlo + np.asarray(host.dims, dtype=float)
        c0 = np.maximum(lo, hlo)
        c1 = np.minimum(hi, hhi)
        mine = self.sowner == host_i
        if mine.any():   # floor of the cavity: the tallest host solid under this footprint
            under = (mine & (self.smin[:, 0] < c1[0] - EPS) & (self.smax[:, 0] > c0[0] + EPS)
                          & (self.smin[:, 1] < c1[1] - EPS) & (self.smax[:, 1] > c0[1] + EPS))
            if under.any():
                c0[2] = max(c0[2], float(self.smax[under, 2].max()))
        c0[2] = min(c0[2], hhi[2])
        ext = (c1[0] - c0[0], c1[1] - c0[1], hhi[2] - c0[2])   # up to the host's lid in z
        return {"item_id": host.item_id,
                "cavity": [round(float(v), 9) for v in (*c0, *ext)]}

    def _commit(self, item: Item, o, k: int, lo) -> None:
        d = np.asarray(o.dims, dtype=float)
        lo = np.round(np.asarray(lo, dtype=float), 9)
        hi = np.round(lo + d, 9)
        center = (lo + hi) / 2.0
        nested_in = self._nested_in(lo, hi)
        owner = len(self.placements)
        for blo, bhi in oriented_solid_boxes(item, lo, d, o.name):
            self._add_solid(blo, bhi, item.fragile, owner)
        if item.occupied_volume < item.bbox_volume - EPS:
            self._hosts.append(owner)
        self.total_mass += item.mass
        self.mass_moment += item.mass * center
        self.total_bbox_vol += item.bbox_volume
        self.total_occupied_vol += item.occupied_volume
        self.vol_moment += item.bbox_volume * center
        self.total_true_vol += item.volume
        self.chosen_orient[item.id] = k
        self.placements.append(Placement(
            item_id=item.id, shape=item.shape, position=tuple(lo.tolist()), dims=tuple(d.tolist()),
            center=tuple(center.tolist()), orientation=o.name, axis=o.axis,
            radius=item.radius, height=item.height, mass=item.mass, fragile=item.fragile,
            volume=item.volume, priority=item.priority, scan_shape=item.scan_shape,
            scan_yaw_deg=item.scan_yaw_deg, nested_in=nested_in))

    # ------------------------------------------------------------------ summaries
    def max_extent(self) -> np.ndarray:
        if len(self.smax) > self.n_obstacles:
            return self.smax[self.n_obstacles:].max(axis=0)
        return np.zeros(3)


def decode(container: Container, items, seq, orient=None, params: Optional[DecoderParams] = None) -> PackState:
    """Decode a genome (item sequence + optional orientation index per item) into a PackState."""
    st = PackState(container, params)
    for i in seq:
        st.place(items[i], None if orient is None else orient[i])
    return st
