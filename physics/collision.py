"""OBB-vs-OBB collision test via the Separating Axis Theorem (SAT).

Assumptions
-----------
- Both boxes are convex, oriented bounding boxes (`OBB` from `physics.geometry`):
  a center, 3 orthonormal world-space axes (columns of `axes`), and 3 half-extents.
  This module tests boxes only, not the underlying meshes -- an OBB is a
  conservative/approximate proxy for whatever object it represents.
- Coordinate system matches `physics.schema`: meters, X=right/Y=up/Z=forward,
  right-handed. SAT itself is coordinate-free; this just fixes units for
  `penetration_depth_m`.
- Two convex polyhedra are separated iff there exists a separating axis among:
  the 3 face normals of A, the 3 face normals of B, and the 9 axes formed by
  cross products of A's edges with B's edges (Gottschalk et al.). For boxes,
  face normals are just the box's own local axes. 15 axes total, checked in a
  fixed order (A's axes, B's axes, 9 cross products) -- ties in the "smallest
  overlap" search keep whichever axis was checked first.

The math (Ericson, *Real-Time Collision Detection* §4.4.1 / Gottschalk's OBBTree)
------------------------------------------------------------------------------
Everything is expressed in A's frame, so no axis is ever built and normalized
explicitly (the old formulation did, which is what made near-parallel edges
degenerate). For a pair (A, B), with `a`/`b` the half-extent vectors:

    R     = A.axes.T @ B.axes          R[i][j] = a_i . b_j
    t     = A.axes.T @ (B.c - A.c)     B's center in A's frame
    AbsR  = |R| (+ EPS_PARALLEL on the cross-axis terms, see below)

- Face axis a_i (i = 0..2): separated iff `|t_i| > a_i + sum_j b_j*AbsR[i][j]`.
- Face axis b_j (j = 0..2): separated iff
  `|sum_i t_i*R[i][j]| > b_j + sum_i a_i*AbsR[i][j]`.
- Cross axis a_i x b_j (9 of them), all quantities implicitly scaled by
  `||a_i x b_j|| = sqrt(max(0, 1 - R[i][j]^2))`:
      ra   = a_{i+1}*AbsR[i+2][j] + a_{i+2}*AbsR[i+1][j]
      rb   = b_{j+1}*AbsR[i][j+2] + b_{j+2}*AbsR[i][j+1]
      proj = |t_{i+2}*R[i+1][j] - t_{i+1}*R[i+2][j]|          (indices mod 3)
  separated iff `proj > ra + rb`.

`EPS_PARALLEL` (1e-8) is added to the `AbsR` entries used by the *cross-axis*
radii only. Those radii are sums of products of half-extents with `R` entries
that all go to zero exactly when a_i is parallel to b_j, so for near-parallel
edge pairs `ra + rb` collapses to float noise while `proj` is a difference of
two nearly-equal products -- the comparison becomes meaningless and can invent
a separation. The padding makes the test *conservative* there (it can report a
collision for boxes that are separated only along a near-degenerate axis --
the accepted, documented tradeoff) instead of the old behaviour, which
*skipped* such axes entirely. The 6 face-axis radii are NOT padded: they are
O(half-extent) and never suffer that cancellation, and padding them would
perturb every reported face-axis penetration depth (the common case) by
~1e-8 * sum(half_extents).

Epsilon semantics
------------------
`epsilon` is a distance-space slack applied to the *overlap along the current
axis*, in meters, not to the SAT separation test that decides collision.
Concretely: for each axis, the overlap is `radius_sum - center_dist`; for the
9 cross axes that difference comes out scaled by `||a_i x b_j||`, so it is
divided by that norm to put all 15 overlaps in meters before any comparison.
If, for ANY axis, `overlap <= epsilon`, the boxes are NOT colliding (the boxes
are separated, touching, or overlapping only by a sliver smaller than epsilon)
-- that axis is still a valid separating axis for reporting purposes. Two
boxes touching exactly (overlap == 0) therefore report `colliding=False` with
`penetration_depth_m=0.0` for any epsilon >= 0. An overlap of
`epsilon < overlap` on every one of the 15 axes reports `colliding=True`, with
`penetration_depth_m` = the minimum such overlap across all axes (the MTV
magnitude) and `axis` its (unit) direction. This also means a razor-thin true
overlap (e.g. 1e-4 m) with a small epsilon (e.g. 1e-6) still reports as
colliding, while numerically-touching boxes (overlap ~ 1e-9 from float error)
correctly report as not colliding, at the default epsilon=1e-6.

A cross axis whose norm is <= `_CROSS_AXIS_MIN_NORM` (1e-8, i.e. genuinely
parallel edges) is excluded from the MTV search, since its overlap cannot be
converted to meters without dividing by ~0. It is excluded from the collision
decision too, which costs nothing: under the padded test such an axis can
never be the separating one (`proj` is exactly 0 when the edges are parallel,
while `ra + rb >= EPS_PARALLEL * ...` > 0).

Complexity / performance
------------------------
`check_collision`: O(1) -- 15 fixed axes, O(1) work each, no vertices and no
explicit axis construction (only the single winning MTV axis is built in world
space). It is a thin m=1 wrapper around `check_pairs`, so there is exactly one
implementation of the SAT math in this module.
`check_pairs(geom, pairs)`: all m pairs at once, numpy-batched, no Python loop
over pairs -- the 15 axis tests are (m, 15) array ops.
`aabb_overlap`: O(1) -- 8 vertices per box via `obb_vertices`, one bbox each.
`collide_scene(geom)`: O(n^2) broad phase (one numpy broadcast in
`scene_geometry.aabb_candidate_pairs`) plus one batched narrow phase over the
surviving pairs. There is still no spatial index; at hackathon scale (n <= ~40)
the O(n^2) broad phase is microseconds.
Measured (python 3.14 / numpy 2.4, single core): single-pair `check_collision`
~210 us colliding / ~160 us separated (vs ~700 us / ~490 us for the old
per-axis Python loop); `collide_scene` on a dense 20-object scene (100 of the
190 pairs overlapping) ~1.1 ms per call, vs ~27 ms for the same pairs through
per-pair `check_collision`, i.e. ~25x. Per-pair cost is ~11 us batched vs
~210 us alone -- numpy call overhead dominates at m=1, so batch whenever you
can.

Known failure modes
--------------------
- Boxes only: concave or non-box shapes are not modeled; an OBB may report a
  collision (or lack of one) that the true mesh would not.
- Near-parallel edges: handled by padding, not skipping (see `EPS_PARALLEL`
  above). The cross-axis test for two nearly-parallel edges is numerically
  unreliable, so it is biased towards "not separated": two boxes separated
  *only* by such a degenerate axis can be misreported as colliding (with a
  small penetration depth). This is the classic SAT edge case, and the bias is
  deliberate -- a false "colliding" is a conservative answer for a packing
  validator, a false "clear" is not.
- Large coordinate magnitudes: float64 has ~15-17 significant decimal digits;
  at coordinates on the order of 1e6 m the sub-millimeter precision this
  module aims for degrades. Not addressed here -- keep scene coordinates near
  the origin (true for a suitcase-packing scene, order 1 m).
- `contact_point` is a documented approximation (see its docstring below), not
  a physically exact contact manifold.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from physics.geometry import OBB, obb_vertices
from physics.scene_geometry import SceneGeometry, aabb_candidate_pairs

EPS_PARALLEL = 1e-8  # padding on cross-axis AbsR terms; makes near-parallel edges conservative
_CROSS_AXIS_MIN_NORM = 1e-8  # below this, a x b is degenerate: no metric depth, excluded from MTV

# (i+1) % 3 and (i+2) % 3 as index tables, hoisted so the batched math never
# recomputes them (used to gather the cross-axis terms for all 9 (i, j)).
_I1 = np.array([1, 2, 0])
_I2 = np.array([2, 0, 1])


@dataclass
class CollisionResult:
    colliding: bool
    penetration_depth_m: float = 0.0
    axis: np.ndarray = None  # unit vector, MTV direction (A -> B convention), or None
    contact_point: np.ndarray = None  # approximate, see check_collision docstring
    a_id: str | None = None  # OBB.id of `a`, for callers that don't thread ids themselves
    b_id: str | None = None  # OBB.id of `b`


def aabb_overlap(a: OBB, b: OBB) -> bool:
    """Cheap world-axis-aligned bounding-box overlap test, for broad-phase pruning.

    Computes each OBB's world AABB from its 8 vertices (O(1): 8 points per box)
    and checks the 3 interval overlaps. This is a *necessary* condition for
    OBB-OBB collision, not sufficient -- always false negatives are impossible,
    but it can return True for non-colliding OBBs (their tight AABBs overlap
    even though the rotated boxes don't). Use it to skip full SAT for pairs
    that are obviously far apart.

    Scene-level equivalent: `scene_geometry.aabb_candidate_pairs(geom, eps)`
    does this for all n^2/2 pairs in one numpy broadcast off the precomputed
    AABBs (and is what `collide_scene` uses). Prefer it whenever you have a
    `SceneGeometry`; this per-pair helper stays for callers that only hold two
    OBBs.
    """
    av = obb_vertices(a)
    bv = obb_vertices(b)
    a_min, a_max = av.min(axis=0), av.max(axis=0)
    b_min, b_max = bv.min(axis=0), bv.max(axis=0)
    return bool(np.all(a_max >= b_min) and np.all(b_max >= a_min))


def _sat_batch(ca, aax, ha, cb, bax, hb, epsilon):
    """Batched Ericson/Gottschalk OBB-OBB SAT over m pairs. See module docstring.

    Inputs are (m, 3) centers / half-extents and (m, 3, 3) axes (columns =
    world unit local axes). Returns
    `(colliding (m,) bool, depth (m,) float, axis (m, 3), contact (m, 3))`;
    `depth`/`axis`/`contact` are only meaningful where `colliding`.
    """
    m = ca.shape[0]
    R = np.einsum("mki,mkj->mij", aax, bax)  # R[i][j] = a_i . b_j
    d = cb - ca
    t = np.einsum("mki,mk->mi", aax, d)  # B's center in A's frame
    absR = np.abs(R)
    absRp = absR + EPS_PARALLEL  # cross-axis terms only (see module docstring)

    # --- 6 face axes: overlap already in meters (the axes are unit vectors). ---
    ovl_a = ha + np.einsum("mij,mj->mi", absR, hb) - np.abs(t)
    ovl_b = hb + np.einsum("mij,mi->mj", absR, ha) - np.abs(np.einsum("mi,mij->mj", t, R))

    # --- 9 cross axes a_i x b_j, in units scaled by ||a_i x b_j||. ---
    ra = ha[:, _I1, None] * absRp[:, _I2, :] + ha[:, _I2, None] * absRp[:, _I1, :]
    rb = hb[:, None, _I1] * absRp[:, :, _I2] + hb[:, None, _I2] * absRp[:, :, _I1]
    proj = np.abs(t[:, _I2, None] * R[:, _I1, :] - t[:, _I1, None] * R[:, _I2, :])
    ovl_c = (ra + rb - proj).reshape(m, 9)
    norm_c = np.sqrt(np.maximum(0.0, 1.0 - R.reshape(m, 9) ** 2))

    overlap = np.concatenate([ovl_a, ovl_b, ovl_c], axis=1)  # (m, 15), axis order = docstring
    norm = np.concatenate([np.ones((m, 6)), norm_c], axis=1)
    usable = norm > _CROSS_AXIS_MIN_NORM  # always True for the 6 face axes
    depth = np.where(usable, overlap / np.where(usable, norm, 1.0), np.inf)  # meters

    k = depth.argmin(axis=1)  # MTV axis index; ties keep the first-checked axis
    rows = np.arange(m)
    best = depth[rows, k]
    colliding = best > epsilon  # any axis with overlap <= epsilon separates
    if not colliding.any():
        zeros = np.zeros((m, 3))  # nothing to report an MTV/contact for
        return colliding, best, zeros, zeros

    # --- World direction of the winning axis only (never all 9 crosses). ---
    kc = np.maximum(k - 6, 0)
    cross = np.cross(aax[rows, :, kc // 3], bax[rows, :, kc % 3])
    cn = np.linalg.norm(cross, axis=1, keepdims=True)
    axis = np.where(
        k[:, None] < 3,
        aax[rows, :, np.where(k < 3, k, 0)],
        np.where(k[:, None] < 6, bax[rows, :, np.where(k < 6, k - 3, 0)],
                 cross / np.where(cn > 0.0, cn, 1.0)),
    )
    axis = axis * np.where(np.einsum("mj,mj->m", axis, d) < 0.0, -1.0, 1.0)[:, None]  # orient A->B

    # --- Approximate contact point: midpoint of the overlap interval on `axis`. ---
    ra_w = np.einsum("mk,mk->m", np.abs(np.einsum("mj,mjk->mk", axis, aax)), ha)
    rb_w = np.einsum("mk,mk->m", np.abs(np.einsum("mj,mjk->mk", axis, bax)), hb)
    a_c = np.einsum("mj,mj->m", axis, ca)
    b_c = np.einsum("mj,mj->m", axis, cb)
    mid = (np.maximum(a_c - ra_w, b_c - rb_w) + np.minimum(a_c + ra_w, b_c + rb_w)) / 2.0
    perp_ref = (ca + cb) / 2.0  # keep the perpendicular component of the centers' midpoint
    contact = perp_ref + (mid - np.einsum("mj,mj->m", axis, perp_ref))[:, None] * axis
    return colliding, best, axis, contact


def check_pairs(
    geom: SceneGeometry, pairs: np.ndarray, epsilon: float = 1e-6
) -> list[CollisionResult]:
    """SAT for every (i, j) row of `pairs` at once, indexing `geom`'s arrays.

    Returns one `CollisionResult` per input row, in input order, `a_id`/`b_id`
    filled from `geom.ids`. Same epsilon semantics and same math as
    `check_collision` -- literally the same code path, batched.
    """
    pairs = np.asarray(pairs, dtype=int).reshape(-1, 2)
    if len(pairs) == 0:
        return []
    i, j = pairs[:, 0], pairs[:, 1]
    colliding, depth, axis, contact = _sat_batch(
        geom.centers[i], geom.axes[i], geom.half_extents[i],
        geom.centers[j], geom.axes[j], geom.half_extents[j], epsilon,
    )
    ids = geom.ids
    return [
        CollisionResult(True, float(depth[p]), axis[p], contact[p], ids[i[p]], ids[j[p]])
        if colliding[p]
        else CollisionResult(False, 0.0, a_id=ids[i[p]], b_id=ids[j[p]])
        for p in range(len(pairs))
    ]


def check_collision(a: OBB, b: OBB, epsilon: float = 1e-6) -> CollisionResult:
    """SAT test for two OBBs. See module docstring for epsilon semantics.

    `axis` is the unit-length minimum-translation-vector (MTV) direction: the
    axis (among the 15 candidates) with the smallest positive overlap, i.e.
    the axis SAT would push along to separate the boxes with least motion.
    It is oriented to point from A's center towards B's center.

    `contact_point` is an approximation: the midpoint, along the MTV axis, of
    the overlapping interval of the two boxes' projections onto that axis,
    offset from A's center. It is a reasonable single point inside the
    overlap region but is NOT a true contact manifold/patch -- for face-face
    contact the real contact region is a polygon, not a point.

    This is a thin m=1 wrapper around the batched `_sat_batch`; use
    `check_pairs`/`collide_scene` when you have more than one pair.
    """
    colliding, depth, axis, contact = _sat_batch(
        a.center[None], a.axes[None], a.half_extents[None],
        b.center[None], b.axes[None], b.half_extents[None], epsilon,
    )
    if not colliding[0]:
        return CollisionResult(colliding=False, penetration_depth_m=0.0, a_id=a.id, b_id=b.id)
    return CollisionResult(True, float(depth[0]), axis[0], contact[0], a.id, b.id)


def collide_scene(
    geom: SceneGeometry, epsilon: float = 1e-6, broad_phase_epsilon: float | None = None
) -> list[CollisionResult]:
    """Every colliding object pair in a scene: AABB broad phase + batched SAT.

    `broad_phase_epsilon` (default: `epsilon`) pads the AABBs in the broad
    phase, so a pair can only be pruned when it is further apart than the
    narrow phase's own slack -- no pair that SAT would call colliding is lost.
    Only colliding results are returned (order: `aabb_candidate_pairs`, i.e.
    ascending (i, j)).
    """
    eps = epsilon if broad_phase_epsilon is None else broad_phase_epsilon
    return [r for r in check_pairs(geom, aabb_candidate_pairs(geom, eps), epsilon) if r.colliding]
