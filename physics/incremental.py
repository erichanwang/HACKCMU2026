"""Incremental placement validation for the packing solver's inner loop.

Problem this solves: `validator.validate_layout` re-validates an ENTIRE scene
every time the solver asks "I have k objects placed, can I add this one
here?", so a solver spends its whole time budget grading arrangements instead
of exploring them. This module keeps the answer up to date as objects arrive
and leave, touching only the objects whose verdict can actually change.

Two modes, and they answer different questions
-----------------------------------------------
1. EXACT whole-scene mode -- `commit` / `remove` / `validate` /
   `try_place_exact`. `validate()` returns the same dict `validate_layout`
   would return for `to_scene()`, byte for byte, and `try_place_exact(obj)`
   the same dict for the scene plus `obj`. This is the mode to build a
   physics-guided search on: the verdict is the real one, so a placement the
   solver accepts is a placement the final pipeline accepts.
2. APPROXIMATE per-object mode -- `try_place` / `place`. Older, cheaper in
   code, and NOT equal to `validate_layout`: it reports only the new object's
   own violations, with the documented approximations under "Divergences"
   below. Kept because callers depend on its shape; prefer mode 1 for anything
   whose answer has to agree with the full pipeline.

Exact mode: what stays incremental, and why it is exact
--------------------------------------------------------
Each pipeline stage is decomposed by what its result actually depends on:

- geometry: per object. `_precompute_row` reproduces `scene_geometry.
  precompute`'s arithmetic at n=1, bit for bit (NOT `obb_vertices`, which
  differs in the last ulp -- see that function).
- containment: per object; cached at commit, never revisited.
- collisions: per PAIR. Only pairs involving the new object are evaluated,
  through a uniform-grid broad phase, then one batched `collision._sat_batch`
  over the survivors. Nothing already cached can change.
- support: an object's support depends only on what is UNDER it, so adding X
  re-derives X plus the objects that now rest on X, and then propagates only
  the chain-instability flag up through everything standing on those.
- constraints: orientation is per object (cached at commit). The transitive
  load pass is O(n + edges) and runs only when some object is fragile, heavy
  or cannot_support_weight -- with no such flag in the scene the load is dead
  code in `check_constraints` too.
- metrics: genuinely O(n^2) and nothing `valid`/`score` depends on, so
  `validate(metrics=False)` skips it. That is the inner-loop setting. When
  metrics ARE asked for, `scene_metrics(precompute(to_scene()))` runs -- the
  same two calls `validate_layout` makes, rather than a hand-assembled
  `SceneGeometry` that would have to be updated every time that dataclass
  grows a field.

Exactness is not argued, it is tested: tests/test_incremental_equivalence.py
runs several hundred random scenes (mixed rigidity, random orientation, every
constraint flag) through both paths and compares the JSON. Two fast paths take
a shortcut whose equality is proved rather than approximated, and both are
covered there: containment is settled from the object's AABB when the
container is world-axis-aligned, and an axis-aligned pair is *cleared* without
SAT when its three face overlaps do not exceed epsilon (the 9 cross axes
reduce to those three plus a positive multiple of `collision.EPS_PARALLEL`).

Prisms
-------
An object carrying a scanned `footprint` is a convex PRISM, not a box, and the
cached rows here are its OBB. Rather than answer approximately, `validate` /
`try_place_exact` detect any committed prism and hand the whole scene to
`validate_layout` -- correct, just not incremental. Making prisms incremental
means caching footprint polygons and prism ring vertices per object and
routing the narrow phase, support footprint and COM through them; until then
the exactness guarantee below is a BOX guarantee.

Equality holds at this validator's default epsilons, which are
`validate_layout`'s: epsilon=1e-6, contact_eps=1e-3, floating_threshold=0.05.
Construct it with other values and the two paths are answering different
questions.

Approximate mode contract
--------------------------
`try_place(obj) -> dict` and `place(obj) -> dict` both return a dict shaped
EXACTLY like `validate_layout`'s result -- `{"valid", "score", "violations",
"warnings"}`, using the same violation/warning types, dict shapes, and
severity formulas (see `physics.validator` module docstring) -- but the
violations/warnings describe ONLY `obj` against the currently-committed
state, not the whole scene. `score` uses `n = 1` (one object per call):
`score = max(0, 1 - sum(min(1, severity) for v in violations))`. Never raises:
malformed input (bad geometry, from `obb_from`'s ValueError, or a duplicate
id) becomes a `MALFORMED_GEOMETRY` violation, exactly like `validate_layout`.

What is NOT re-checked (approximate mode only)
----------------------------------------------
Previously committed objects' mutual validity (their pairwise collisions,
containment, support, orientation) is NOT re-verified on every query -- that
was already checked when each was placed, and this is the caller's
responsibility to have upheld (only place objects whose own `try_place` was
valid, or that you explicitly accept via `force=True`).

Load is the documented exception, and it runs BOTH ways:
- the new object resting ON something can overload it, so the new object's
  direct supporters' `cannot_support_weight` is checked and
  `FRAGILE_OBJECT_OVERLOADED` emitted for the SUPPORTER's id;
- the new object BEING a supporter can overload the new object, so
  `_objects_resting_on` looks for already-committed objects that come to
  rest on it. (Without that, a fragile pedestal slid in under an existing
  load reported clean while `validate_layout` reported it crushed.)
The weights reported are DIRECT masses, not the full pipeline's area-weighted
transitive load, so the number differs from `validate_layout`'s even when the
verdict agrees. Nothing else about already-committed objects is re-derived
(e.g. a supporter's own `HEAVY_ON_TOP` warning is not recomputed).

Recommendation: use exact mode, or run `validate_layout(pv.to_scene())` ONE
time at the end as the authoritative check -- approximate mode optimizes the
hot inner loop, it does not replace the full pipeline's guarantees.

Divergences from `physics.validator` accepted for APPROXIMATE mode
-------------------------------------------------------------------
(None of these apply to `validate` / `try_place_exact`.)
- This module deliberately trades exactness for O(k) speed. The full
  pipeline (`physics.support`, `physics.constraints`) uses exact convex-hull
  contact polygons, chain-instability propagation and TRANSITIVE load
  (a shoe on a bag on a laptop loads the laptop); here support and constraint
  logic is replicated with cheap AABB / direct-contact approximations in
  small private helpers. Geometry (`check_collision`, `check_containment`)
  and `physics.compressibility` ARE the shared implementations.
- Support footprint: this module uses the object's full XZ-AABB
  (`aabb_min`/`aabb_max` columns 0, 2) as its footprint and its supporters'
  XZ-AABBs as contact patches -- identical to the exact polygons for
  axis/yaw-aligned boxes (the common packing case, and every test fixture),
  an over-estimate for a rolled/pitched object.
- `support_ratio`'s covered area is the SUM of each clipped supporter
  rectangle's area, clamped to 1.0 -- not support.py's exact coordinate
  -compression union. This double-counts area where two supporters (or a
  supporter and the floor) overlap each other under the same object, which
  would under-report `support_ratio` as artificially high (never inflates a
  clearly floating object to "supported", since with zero true overlap the
  sum is still zero, and the ratio is only ever driven toward 1.0, which is
  the conservative direction for a solver deciding "yes, keep going").
  ponytail: sum-of-clipped-rects, O(#supporters); upgrade to the coordinate
  -compression union (support.py's `_union_area`) if double-counted stacks of
  overlapping supporters become common at hackathon object counts.
- One `contact_eps` (default 1e-3 m, matching `support.py`'s default) is used
  for BOTH "is this resting on the floor/another object" AND the
  fragile/heavy direct-contact rule. `validate_layout` actually uses TWO
  different epsilons for this: `support.py`'s 1e-3 for support, and
  `constraints.py`'s separate default of 0.02 for fragile/heavy contact.
  `PlacementValidator.__init__` only exposes one `contact_eps`, so this
  module intentionally unifies them -- reusing the supporters already found
  for the support check, rather than re-deriving contact with a second,
  looser epsilon. Net effect: this module is slightly STRICTER than
  `validate_layout` about what counts as "resting on" for fragile/heavy
  purposes (misses only objects resting with a 1mm-2cm gap, which real flush
  -packed items never have -- test fixtures all stack flush).
- Orientation checks (`keep_upright`/`orientation_lock`) replicate
  `constraints.py`'s ~10 lines of angle math directly rather than importing
  `check_constraints` (same "under concurrent rewrite" reasoning as above).

Complexity
-----------
Exact mode `commit`/`remove`: O(neighbours + objects resting on the new one),
not O(k) and not O(k^2). The broad phase is a uniform grid (`_cells_of` /
`_neighbors`) keyed on the world AABB, so nothing scans the whole scene;
objects whose AABB spans more than `_GRID_MAX_CELLS` cells go in an oversized
list that is always considered, which keeps the grid correct at any cell size.
`validate` itself is an O(k) walk assembling cached entries (it builds a dict
only for objects that actually have something to report) plus, when
`metrics=True`, one O(k^2) `scene_metrics` pass.

Approximate mode `try_place`/`place`: O(k) -- a vectorized AABB prefilter over
the cached (k, 3) arrays, then `check_collision` only on the survivors.

Measured on one (heavily loaded) machine, dense 0.20 m boxes on a 0.21 m grid,
`validate_layout` for the same scene as the baseline:

    k=20   valid candidate      ~0.32 ms exact   vs ~5.5 ms full   (17x)
    k=50   valid candidate      ~0.30 ms exact   vs ~8.7 ms full   (29x)
    k=50   overlapping reject   ~1.05 ms exact   vs ~6.6 ms full   (6x)

The exact path is flat in k where the full pipeline is not, which is the whole
point. Rejects cost more because a real overlap has to go through
`_sat_batch` for its exact depth/axis/contact -- the one place where exactness
is expensive.

Cache
-----
One `_Rec` per committed object: its `Object`/`OBB`, precompute-identical
axes/half-extents/centre/vertices, the AABB as plain floats, support.py's
contact bitmasks and bottom/top footprints, its containment and orientation
entries, its support result, and its edges in the two resting graphs (support
tolerance 1e-3, constraints tolerance 2e-2). The stacked `(k, ...)` arrays the
approximate path wants are rebuilt lazily from the records rather than
vstacked on every commit.
"""
from __future__ import annotations

import math

import numpy as np

from physics.collision import _sat_batch, check_collision
from physics.compressibility import (
    axis_projected_extent_m,
    combined_collision_allowance_m,
    container_wall_allowance_m,
)
from physics.constraints import DEFAULT_CONTACT_EPS_M as CONSTRAINT_CONTACT_EPS_M
from physics.constraints import _up_axis_cosines
from physics.containment import _build_result, _containment_arrays, check_containment
from physics.geometry import OBB, SIGNS, obb_from, obb_vertices
from physics.metrics import scene_metrics
from physics.scene_geometry import MalformedSceneError, _quats_to_matrices, precompute
from physics.schema import Container, Object, Scene
from physics.validator import validate_layout

# Private helpers borrowed from `physics.support` on purpose: the exact
# incremental path below has to reproduce `check_support`'s polygon math
# BIT-FOR-BIT, and re-implementing `_hull`/`_clip`/`_poly_area`/`_signed_dist`
# here would be a second copy free to drift. tests/test_incremental_equivalence
# .py is the tripwire if support.py's internals change under us.
from physics.support import (
    DEGENERATE_AREA_M2,
    FLOATING_MARGIN_SENTINEL_M,
    TOUCH_TOL_M,
    _axis_rect,
    _BIT_INDICES,
    _clip,
    _FACE_CYCLES,
    _face_hull,
    _hull,
    _poly_area,
    _signed_dist,
    _xz_hull,
)

Rect = tuple[float, float, float, float]  # xmin, xmax, zmin, zmax

WORLD_UP = np.array([0.0, 1.0, 0.0])
DEFAULT_ANGLE_TOL_DEG = 15.0
_WALL_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _vec3(v) -> list[float]:
    return [float(v[0]), float(v[1]), float(v[2])]


def _sort_key(entry: dict):
    oid = entry.get("object")
    if oid is None:
        objs = entry.get("objects")
        oid = min(objs) if objs else ""
    return (entry["type"], oid)


def _malformed(object_id, exc) -> dict:
    return {
        "valid": False,
        "score": 0.0,
        "violations": [{"type": "MALFORMED_GEOMETRY", "object": object_id, "detail": str(exc)}],
        "warnings": [],
    }


# --- tiny rectangle helpers, replicated (not imported) from support.py/
# constraints.py -- see module docstring "Divergences" for why. ---


def _rect_intersection(a: Rect, b: Rect) -> Rect | None:
    xmin, xmax = max(a[0], b[0]), min(a[1], b[1])
    zmin, zmax = max(a[2], b[2]), min(a[3], b[3])
    if xmin >= xmax or zmin >= zmax:
        return None
    return (xmin, xmax, zmin, zmax)


def _rect_area(r: Rect) -> float:
    return max(0.0, r[1] - r[0]) * max(0.0, r[3] - r[2])


def _merge_rect(rects: list[Rect]) -> Rect:
    return (
        min(r[0] for r in rects),
        max(r[1] for r in rects),
        min(r[2] for r in rects),
        max(r[3] for r in rects),
    )


def _signed_dist_to_rect(px: float, pz: float, r: Rect) -> float:
    xmin, xmax, zmin, zmax = r
    if xmin <= px <= xmax and zmin <= pz <= zmax:
        return min(px - xmin, xmax - px, pz - zmin, zmax - pz)
    dx = max(xmin - px, 0.0, px - xmax)
    dz = max(zmin - pz, 0.0, pz - zmax)
    return -float(np.hypot(dx, dz))


def _rects_overlap(a: Rect, b: Rect) -> bool:
    ax0, ax1, az0, az1 = a
    bx0, bx1, bz0, bz1 = b
    return ax0 < bx1 and bx0 < ax1 and az0 < bz1 and bz0 < az1


# --- exact-path helpers (see "Exact whole-scene mode" in the module docstring) ---

_GRID_MAX_CELLS = 64  # an AABB spanning more cells than this goes in the oversized list


def _check_object(obj: Object) -> None:
    """`scene_geometry.precompute`'s validity gate, for one object.

    Mirrors precompute's vectorized `ok` mask in plain Python (no numpy call
    on the hot path) and, when it fails, defers to `obb_from` so the raised
    ValueError carries the one canonical message precompute would report."""
    try:
        d, p, q = obj.dimensions, obj.position, obj.rotation
        ok = (
            len(d) == 3
            and len(p) == 3
            and len(q) == 4
            and math.isfinite(d[0]) and d[0] > 0
            and math.isfinite(d[1]) and d[1] > 0
            and math.isfinite(d[2]) and d[2] > 0
            and math.isfinite(p[0]) and math.isfinite(p[1]) and math.isfinite(p[2])
        )
        if ok:
            qn = q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]
            ok = math.isfinite(qn) and qn >= 1e-12
    except (TypeError, ValueError):
        ok = False
    if not ok:
        obb_from(obj)  # raises the canonical ValueError
        raise ValueError(f"{obj.id}: invalid geometry")  # pragma: no cover - defensive


_EYE = np.eye(3)


def _precompute_row(obj: Object) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """`precompute`'s arithmetic for a single object: (axes, half_extents,
    center, vertices).

    Deliberately NOT `obb_from`/`obb_vertices`: those use a different (but
    mathematically equivalent) expression order and differ from precompute's
    batched einsum in the last ulp, which is enough to move a penetration
    depth or a hull area. Running precompute's own expressions at n=1 is
    bit-identical (tests/test_incremental_equivalence.py asserts it).

    Identity rotation (most of a packing scene) skips the quaternion matrix
    and the einsum: `_quats_to_matrices` on (0, 0, 0, 1) is exactly `eye(3)`,
    and the einsum then reduces to `SIGNS * half_extents` -- its other terms
    are exact zeros, which add without rounding. Same doubles, ~4 numpy calls
    instead of ~19."""
    q = obj.rotation
    half_extents = np.array(obj.dimensions, dtype=float) / 2.0
    center = np.array(obj.position, dtype=float)
    if q[0] == 0.0 and q[1] == 0.0 and q[2] == 0.0 and q[3] == 1.0:
        return _EYE, half_extents, center, center + SIGNS * half_extents
    quats = np.array([q], dtype=float).reshape(1, 4)
    axes = _quats_to_matrices(quats)
    vertices = center[None, :] + np.einsum(
        "vk,nk,njk->nvj", SIGNS, half_extents[None, :], axes
    )
    return axes[0], half_extents, center, vertices[0]


def _xz_area(a: "_Rec", b: "_Rec") -> float:
    """`scene_geometry.xz_overlap_area` on two cached records."""
    dx = min(a.hi[0], b.hi[0]) - max(a.lo[0], b.lo[0])
    dz = min(a.hi[2], b.hi[2]) - max(a.lo[2], b.lo[2])
    if dx <= 0.0 or dz <= 0.0:
        return 0.0
    return dx * dz


def _adjacent_heavy(a: "_Rec", b: "_Rec") -> bool:
    """constraints.py's `fragile` adjacency test: footprints overlap AND the
    two objects occupy overlapping Y ranges (within the contact tolerance).
    Kept textually parallel with `constraints._adjacent_at_same_height`."""
    if _xz_area(a, b) <= 0.0:
        return False
    tol = CONSTRAINT_CONTACT_EPS_M
    return a.lo[1] - tol <= b.hi[1] and b.lo[1] - tol <= a.hi[1]


class _Rec:
    """Everything cached for one committed object. Pure per-object state plus
    its edges in the two resting graphs; nothing here depends on objects it
    does not touch, which is what makes add/remove local."""

    __slots__ = (
        "obj", "obb", "axes", "he", "center", "verts", "lo", "hi", "xz", "center_xz", "aa",
        "low_bits", "high_bits", "bottom_pts", "bottom_hull", "bottom_area", "top_hull",
        "floored", "inside_floor", "cells", "contain", "orient", "sup",
        "below", "above", "below_c", "above_c",
    )


class PlacementValidator:
    """Incremental per-object validator for one Container. See module docstring."""

    def __init__(
        self,
        container: Container,
        *,
        epsilon: float = 1e-6,
        contact_eps: float = 1e-3,
        floating_threshold: float = 0.05,
    ):
        self.container = container
        self.epsilon = epsilon
        self.contact_eps = contact_eps
        self.floating_threshold = floating_threshold

        self._container_obb = obb_from(container)
        self._container_denom = max(1e-9, float(np.min(self._container_obb.half_extents)))
        container_verts = obb_vertices(self._container_obb)
        self._floor_y = float(container_verts[:, 1].min())
        self._floor_rect: Rect = (
            float(container_verts[:, 0].min()),
            float(container_verts[:, 0].max()),
            float(container_verts[:, 2].min()),
            float(container_verts[:, 2].max()),
        )

        # exact-path container constants (support.py's floor footprint)
        self._container_vertices = container_verts
        self._floor_hull = _xz_hull(container_verts[np.argsort(container_verts[:, 1])[:4]])
        self._floor_axis_rect = _axis_rect(self._floor_hull)
        # A world-axis-aligned container lets containment be settled from the
        # object's AABB alone (see `_exact_containment`); the general path runs
        # otherwise.
        self._container_aligned = bool(np.array_equal(self._container_obb.axes, _EYE))
        self._container_center_f = tuple(self._container_obb.center.tolist())
        self._container_half_f = tuple(self._container_obb.half_extents.tolist())

        self._ids: list[str] = []
        self._index: dict[str, int] = {}
        self._rec: dict[str, _Rec] = {}

        # legacy vectorized cache for the approximate per-object path; rebuilt
        # from `_rec` on demand instead of vstacked on every commit.
        self._vertices = np.zeros((0, 8, 3))
        self._aabb_min = np.zeros((0, 3))
        self._aabb_max = np.zeros((0, 3))
        self._arrays_dirty = False

        # uniform-grid broad phase: cell key -> ids. Objects whose AABB spans
        # more than _GRID_MAX_CELLS cells sit in `_oversized` and are always
        # considered (correct for any cell size, only the constant changes).
        self._cell = max(1e-3, max(float(d) for d in container.dimensions) / 8.0)
        self._grid: dict[tuple[int, int, int], set[str]] = {}
        self._oversized: set[str] = set()

        # exact-path caches: pair key (lower-index id, higher-index id) ->
        # (is_violation, entry); constraint stage output; both invalidated by
        # add/remove.
        self._coll: dict[tuple[str, str], tuple[bool, dict]] = {}
        self._con_cache: tuple[list[dict], list[dict]] | None = None
        # Objects carrying a scanned `footprint` are convex PRISMS, not boxes.
        # Every cached row here is the object's OBB, so the exact path cannot
        # speak for them; `validate` falls back to the full pipeline instead of
        # answering wrongly. See "Prisms" in the module docstring.
        self._prisms = 0

    @property
    def placed_ids(self) -> list[str]:
        return list(self._ids)

    def to_scene(self) -> Scene:
        return Scene(container=self.container, objects=[self._rec[oid].obj for oid in self._ids])

    def try_place(self, obj: Object) -> dict:
        result, _ = self._evaluate(obj)
        return result

    def place(self, obj: Object, *, force: bool = False) -> dict:
        result, obb = self._evaluate(obj)
        if obb is not None and (result["valid"] or force):
            self.commit(obj)
        return result

    def remove(self, object_id: str) -> None:
        """Remove one committed object. Only the objects whose verdicts can
        actually change (the ones that were resting on it) are re-derived."""
        idx = self._index.pop(object_id)
        rec = self._rec.pop(object_id)
        del self._ids[idx]
        self._index = {oid: i for i, oid in enumerate(self._ids)}
        self._arrays_dirty = True

        if rec.cells is None:
            self._oversized.discard(object_id)
        else:
            for cell in rec.cells:
                bucket = self._grid.get(cell)
                if bucket is not None:
                    bucket.discard(object_id)
                    if not bucket:
                        del self._grid[cell]

        for key in [k for k in self._coll if object_id in k]:
            del self._coll[key]

        dirty: set[str] = set()
        for other in rec.below:
            self._rec[other].above.discard(object_id)
        for other in rec.above:
            self._rec[other].below.discard(object_id)
            dirty.add(other)
        for other in rec.below_c:
            self._rec[other].above_c.discard(object_id)
        for other in rec.above_c:
            self._rec[other].below_c.pop(object_id, None)

        self._con_cache = None
        if getattr(rec.obj, "footprint", None) is not None:
            self._prisms -= 1
        self._refresh_support(dirty)

    def incremental_metrics(self, obj: Object) -> dict:
        """Cheap solver feedback for scoring a candidate position -- does not
        run the validation pipeline. Raises ValueError (same as `obb_from`)
        on malformed `obj`; unlike `try_place`, this is a raw metrics helper.

        `nearest_neighbor_gap_m`: AABB gap (meters) to the nearest committed
        object -- 0.0 if AABBs overlap, None if nothing is committed yet.
        `wall_clearance_m`: min, over all 8 vertices and all 3 container
        wall-axes, of the container half-extent minus the vertex's absolute
        container-local coordinate on that axis (negative if penetrating).
        """
        self._ensure_arrays()
        obb = obb_from(obj)
        verts = obb_vertices(obb)
        obj_min, obj_max = verts.min(axis=0), verts.max(axis=0)

        if len(self._ids) == 0:
            nn_gap = None
        else:
            gap_per_axis = np.maximum(
                0.0,
                np.maximum(self._aabb_min - obj_max[None, :], obj_min[None, :] - self._aabb_max),
            )
            nn_gap = float(np.linalg.norm(gap_per_axis, axis=1).min())

        local = (verts - self._container_obb.center) @ self._container_obb.axes
        wall_clearance = float((self._container_obb.half_extents[None, :] - np.abs(local)).min())

        return {"nearest_neighbor_gap_m": nn_gap, "wall_clearance_m": wall_clearance}

    # ------------------------------------------------------------------
    # exact whole-scene mode (identical to physics.validator.validate_layout)
    # ------------------------------------------------------------------

    def commit(self, obj: Object) -> None:
        """Add `obj` to the scene, updating only what its arrival can change.

        Raises `MalformedSceneError` (a ValueError) for a duplicate id or bad
        geometry -- exactly what `scene_geometry.precompute` would raise for
        the same scene. `place()` routes through here, so both the approximate
        and the exact caches stay in step.
        """
        oid = obj.id
        if oid in self._rec or oid == self.container.id:
            raise MalformedSceneError(oid, f"duplicate object id: {oid!r}")
        _check_object(obj)

        rec = _Rec()
        rec.obj = obj
        rec.axes, rec.he, rec.center, rec.verts = _precompute_row(obj)
        rec.aa = rec.axes is _EYE
        rec.obb = OBB(center=rec.center, axes=rec.axes, half_extents=rec.he, id=oid)
        verts = rec.verts
        rec.lo = tuple(verts.min(axis=0).tolist())
        rec.hi = tuple(verts.max(axis=0).tolist())
        rec.xz = [tuple(p) for p in verts[:, ::2].tolist()]
        rec.center_xz = (float(rec.center[0]), float(rec.center[2]))

        # support.py's contact bitmasks / footprints, per object
        eps = self.contact_eps
        vy = verts[:, 1].tolist()
        ymin, ymax = rec.lo[1], rec.hi[1]
        rec.low_bits = sum(1 << k for k in range(8) if vy[k] <= ymin + eps)
        rec.high_bits = sum(1 << k for k in range(8) if vy[k] >= ymax - eps)
        rec.bottom_pts = [rec.xz[k] for k in _BIT_INDICES[rec.low_bits]]
        face = _face_hull(rec.xz, _FACE_CYCLES.get(rec.low_bits))
        rec.bottom_hull = _hull(rec.bottom_pts) if face is None else face
        rec.bottom_area = _poly_area(rec.bottom_hull)
        rec.top_hull = None
        rec.floored = abs(ymin - self._floor_y) <= eps
        fr = self._floor_axis_rect
        rec.inside_floor = fr is not None and (
            rec.lo[0] >= fr[0] and rec.hi[0] <= fr[1] and rec.lo[2] >= fr[2] and rec.hi[2] <= fr[3]
        )

        rec.contain = self._exact_containment(obj, rec.obb, verts, rec.lo, rec.hi)
        rec.orient = self._exact_orientation(obj, rec.axes)
        rec.sup = None
        rec.below, rec.above = set(), set()
        rec.below_c, rec.above_c = {}, set()

        self._index[oid] = len(self._ids)
        self._ids.append(oid)
        self._rec[oid] = rec
        if getattr(obj, "footprint", None) is not None:
            self._prisms += 1
        self._arrays_dirty = True
        self._con_cache = None

        cands = self._neighbors(
            rec.lo, rec.hi, (self.epsilon, max(self.contact_eps, CONSTRAINT_CONTACT_EPS_M,
                                               self.epsilon), self.epsilon)
        )
        cands.discard(oid)
        dirty = self._link(rec, cands)
        self._exact_collisions(rec, cands)
        rec.cells = self._cells_of(rec.lo, rec.hi)
        if rec.cells is None:
            self._oversized.add(oid)
        else:
            for cell in rec.cells:
                self._grid.setdefault(cell, set()).add(oid)
        self._refresh_support(dirty | {oid})

    def validate(self, *, metrics: bool = True) -> dict:
        """The whole scene's verdict, assembled from the incremental caches.

        Byte-identical to `physics.validator.validate_layout(self.to_scene())`
        at this validator's default epsilons -- that equality is the contract,
        and tests/test_incremental_equivalence.py enforces it over random
        scenes. `metrics=False` skips `physics.metrics.scene_metrics` (the one
        genuinely O(n^2) stage, and nothing `valid`/`score` depends on) and
        returns `"metrics": {}`; that is the solver-inner-loop setting.

        The returned violation/warning dicts are the cached ones -- read them,
        don't mutate them.
        """
        if self._prisms:
            # Not incremental, but correct: scanned prisms need the footprint
            # narrow phase / footprint support that the cached OBB rows cannot
            # express, so hand the whole scene to the real pipeline.
            result = validate_layout(
                self.to_scene(), floating_threshold=self.floating_threshold
            )
            if not metrics:
                result["metrics"] = {}
            return result

        violations: list[dict] = []
        warnings: list[dict] = []

        for oid in self._ids:
            entry = self._rec[oid].contain
            if entry is not None:
                (violations if entry[0] else warnings).append(entry[1])

        if self._coll:
            idx = self._index
            for key in sorted(self._coll, key=lambda k: (idx[k[0]], idx[k[1]])):
                is_violation, entry = self._coll[key]
                (violations if is_violation else warnings).append(entry)

        for oid in self._ids:
            s = self._rec[oid].sup
            if s["floating"]:
                violations.append(s["floating_entry"])
            elif s["unstable"]:
                warnings.append(s["unstable_entry"])
            elif s["supported_by_unstable"]:
                warnings.append({
                    "type": "UNSTABLE_SUPPORT_CHAIN",
                    "object": oid,
                    "supporting_objects": list(s["supporting_objects"]),
                })

        c_violations, c_warnings = self._exact_constraints()
        violations.extend(c_violations)
        warnings.extend(c_warnings)

        violations.sort(key=_sort_key)
        warnings.sort(key=_sort_key)
        severity_sum = sum(min(1.0, v.get("severity", 1.0)) for v in violations)
        return {
            "valid": len(violations) == 0,
            "score": max(0.0, 1.0 - severity_sum / max(1, len(self._ids))),
            "violations": violations,
            "warnings": warnings,
            "metrics": scene_metrics(precompute(self.to_scene())) if metrics else {},
        }

    def try_place_exact(self, obj: Object, *, metrics: bool = True) -> dict:
        """`validate_layout(Scene(container, placed + [obj]))` without committing.

        Never raises: malformed geometry or a duplicate id comes back as a
        MALFORMED_GEOMETRY result, same as `validate_layout`. This is the call
        a physics-guided solver puts in its inner loop; pass `metrics=False`
        there, nothing in `valid`/`score`/`violations` depends on them.
        """
        try:
            self.commit(obj)
        except ValueError as e:
            result = _malformed(getattr(e, "object_id", obj.id), e)
            result["metrics"] = {}
            return result
        try:
            return self.validate(metrics=metrics)
        finally:
            self.remove(obj.id)

    # --- broad phase -------------------------------------------------

    def _cells_of(self, lo, hi) -> list[tuple[int, int, int]] | None:
        c = self._cell
        spans = []
        total = 1
        for k in range(3):
            a = int(math.floor(lo[k] / c))
            b = int(math.floor(hi[k] / c))
            total *= b - a + 1
            if total > _GRID_MAX_CELLS:
                return None
            spans.append((a, b))
        (x0, x1), (y0, y1), (z0, z1) = spans
        return [
            (x, y, z)
            for x in range(x0, x1 + 1)
            for y in range(y0, y1 + 1)
            for z in range(z0, z1 + 1)
        ]

    def _neighbors(self, lo, hi, pad) -> set[str]:
        """Ids whose AABB may lie within `pad` of this one, from the grid."""
        cells = self._cells_of(
            (lo[0] - pad[0], lo[1] - pad[1], lo[2] - pad[2]),
            (hi[0] + pad[0], hi[1] + pad[1], hi[2] + pad[2]),
        )
        if cells is None:
            return set(self._ids)
        out = set(self._oversized)
        grid = self._grid
        for cell in cells:
            bucket = grid.get(cell)
            if bucket:
                out |= bucket
        return out

    # --- per-stage incremental updates -------------------------------

    def _exact_containment(self, obj: Object, obb: OBB, verts: np.ndarray, lo, hi):
        """validator.py's containment block for one object -> (is_violation, entry).

        Fast reject: with a world-axis-aligned container, `local` is just
        `vertex - center`, so the largest overshoot on axis j over all 8
        vertices is `max(hi[j] - c[j], c[j] - lo[j]) - half[j]` -- the same
        subtraction and the same comparison `_containment_arrays` makes, on
        the same doubles. If no axis clears epsilon, no vertex penetrates and
        there is nothing to report.
        """
        if self._container_aligned:
            c = self._container_center_f
            half = self._container_half_f
            eps = self.epsilon
            if not (
                max(hi[0] - c[0], c[0] - lo[0]) - half[0] > eps
                or max(hi[1] - c[1], c[1] - lo[1]) - half[1] > eps
                or max(hi[2] - c[2], c[2] - lo[2]) - half[2] > eps
            ):
                return None
        container = self._container_obb
        wall_depth, penetrating = _containment_arrays(
            verts[None], container.center, container.axes, container.half_extents, self.epsilon
        )
        if not penetrating[0].any():
            return None
        res = _build_result(obj.id, verts, wall_depth[0], penetrating[0])
        raw_depth = res.penetration_depth_m
        if obj.rigidity == "rigid":
            effective = dict(res.per_wall_depth_m)
        else:
            effective = {}
            for wall, depth in res.per_wall_depth_m.items():
                axis = container.axes[:, _WALL_AXIS_INDEX[wall[-1]]]
                allowance = container_wall_allowance_m(
                    obj, axis_projected_extent_m(obb, axis)
                )
                effective[wall] = max(0.0, depth - allowance)
        effective_depth = max(effective.values(), default=0.0)
        if effective_depth <= 0.0:
            if raw_depth > 0.0:
                return False, {
                    "type": "SOFT_COMPRESSION",
                    "object": obj.id,
                    "raw_penetration_depth_m": raw_depth,
                    "compressed_depth_m": raw_depth,
                    "walls": sorted(res.per_wall_depth_m),
                }
            return None
        return True, {
            "type": "CONTAINER_PENETRATION",
            "object": obj.id,
            "penetration_depth_m": effective_depth,
            "raw_penetration_depth_m": raw_depth,
            "compressed_depth_m": raw_depth - effective_depth,
            "violated_walls": sorted(w for w, d in effective.items() if d > 0.0),
            "per_wall_depth_m": {w: d for w, d in sorted(effective.items()) if d > 0.0},
            "penetrating_vertices": [_vec3(v) for v in res.penetrating_vertices],
            "severity": _clamp01(effective_depth / self._container_denom),
        }

    def _exact_orientation(self, obj: Object, axes: np.ndarray) -> list[dict]:
        """validator.py's LIQUID_NOT_UPRIGHT / INVALID_ORIENTATION entries for
        one object, via `constraints._up_axis_cosines` so the tilt is the same
        float the batched path produces."""
        c = obj.constraints
        lock = c.orientation_lock
        if not c.keep_upright and lock is None:
            return []
        tilt = math.degrees(math.acos(_up_axis_cosines(axes[None])[0]))
        tol = DEFAULT_ANGLE_TOL_DEG
        out: list[dict] = []

        def entry(type_: str, value: float, lock_: str | None = None) -> dict:
            e = {"type": type_, "object": obj.id}
            if lock_ is not None:
                e["lock"] = lock_
            e["tilt_deg"] = value
            e["tolerance_deg"] = tol
            e["severity"] = _clamp01((value - tol) / max(1e-9, 90.0 - tol))
            return e

        if c.keep_upright and tilt > tol:
            out.append(entry("LIQUID_NOT_UPRIGHT", tilt))
        if lock == "this_side_up":
            if tilt > tol:
                out.append(entry("INVALID_ORIENTATION", tilt, lock))
        elif lock == "flat_only":
            angle = min(tilt, 180.0 - tilt)
            if angle > tol:
                out.append(entry("INVALID_ORIENTATION", angle, lock))
        elif lock == "horizontal":
            angle = abs(90.0 - tilt)
            if angle > tol:
                out.append(entry("INVALID_ORIENTATION", angle, lock))
        return out

    def _exact_collisions(self, rec: _Rec, cands: set[str]) -> None:
        """SAT the new object against its broad-phase neighbours only, in one
        batched `_sat_batch` call, and cache the resulting pair entries.

        A pair's verdict depends on that pair alone, so nothing already cached
        can change -- this is the stage that is exactly incremental for free.
        """
        eps = self.epsilon
        if not cands:
            return
        lo, hi = rec.lo, rec.hi
        idx = self._index
        others = []
        for oid in sorted(cands, key=idx.__getitem__):
            o = self._rec[oid]
            olo, ohi = o.lo, o.hi
            if (
                hi[0] + eps >= olo[0] and ohi[0] + eps >= lo[0]
                and hi[1] + eps >= olo[1] and ohi[1] + eps >= lo[1]
                and hi[2] + eps >= olo[2] and ohi[2] + eps >= lo[2]
            ):
                others.append(o)
        if not others:
            return

        # Axis-aligned pairs (R = identity) can be *cleared* without SAT: the
        # 6 face-axis overlaps reduce to `ha + hb - |dc|` per axis, and each of
        # the 9 cross axes comes out as one of those plus a positive multiple
        # of collision.EPS_PARALLEL, so the min over all 15 is the min over the
        # 3 face overlaps. Same doubles, same `> epsilon` decision, no numpy --
        # and in a packed scene most broad-phase survivors are flush
        # neighbours that SAT would have cleared anyway. Anything that does
        # collide still goes through `_sat_batch` for the exact depth/axis.
        if rec.aa:
            cleared = []
            for o in others:
                if not o.aa:
                    cleared.append(o)
                    continue
                ha, hb, ca, cb = o.he, rec.he, o.center, rec.center
                if min(
                    ha[0] + hb[0] - abs(cb[0] - ca[0]),
                    ha[1] + hb[1] - abs(cb[1] - ca[1]),
                    ha[2] + hb[2] - abs(cb[2] - ca[2]),
                ) > eps:
                    cleared.append(o)
            others = cleared
            if not others:
                return

        m = len(others)
        # `rec` is the newest object, so it always has the highest index: in
        # aabb_candidate_pairs terms it is always B, never A. Keep that, the
        # SAT axis/contact convention is A -> B.
        ca = np.array([o.center for o in others])
        aax = np.array([o.axes for o in others])
        ha = np.array([o.he for o in others])
        cb = np.repeat(rec.center[None], m, axis=0)
        bax = np.repeat(rec.axes[None], m, axis=0)
        hb = np.repeat(rec.he[None], m, axis=0)
        colliding, depth, axis, contact = _sat_batch(ca, aax, ha, cb, bax, hb, eps)

        b_obj = rec.obj
        for k in range(m):
            if not colliding[k]:
                continue
            a = others[k]
            a_obj = a.obj
            raw_depth = float(depth[k])
            if a_obj.rigidity == "rigid" and b_obj.rigidity == "rigid":
                allowance = 0.0
            else:
                allowance = combined_collision_allowance_m(
                    a_obj, b_obj, axis[k], a.obb, rec.obb
                )
            effective_depth = max(0.0, raw_depth - allowance)
            pair = sorted([a_obj.id, b_obj.id])
            key = (a_obj.id, b_obj.id)
            if effective_depth <= 0.0:
                if raw_depth > 0.0:
                    self._coll[key] = (False, {
                        "type": "SOFT_COMPRESSION",
                        "objects": pair,
                        "raw_penetration_depth_m": raw_depth,
                        "compressed_depth_m": raw_depth,
                        "contact_point": _vec3(contact[k]),
                    })
                continue
            denom = max(1e-9, min(min(a_obj.dimensions), min(b_obj.dimensions)))
            self._coll[key] = (True, {
                "type": "OBJECT_COLLISION",
                "objects": pair,
                "penetration_depth_m": effective_depth,
                "raw_penetration_depth_m": raw_depth,
                "compressed_depth_m": raw_depth - effective_depth,
                "contact_point": _vec3(contact[k]),
                "axis": _vec3(axis[k]),
                "severity": _clamp01(effective_depth / denom),
            })

    def _link(self, rec: _Rec, cands: set[str]) -> set[str]:
        """Wire `rec` into both resting graphs (support's 1e-3 contact eps and
        constraints' 2e-2). Returns the already-committed objects whose support
        result changed -- i.e. the ones now resting on `rec`."""
        se = self.contact_eps
        ce = CONSTRAINT_CONTACT_EPS_M
        dirty: set[str] = set()
        rid = rec.obj.id
        for oid in cands:
            o = self._rec[oid]
            area = _xz_area(rec, o)
            if area <= 0.0:
                continue
            on_o = abs(rec.lo[1] - o.hi[1])
            o_on = abs(o.lo[1] - rec.hi[1])
            if on_o <= se:
                rec.below.add(oid)
                o.above.add(rid)
            if o_on <= se:
                o.below.add(rid)
                rec.above.add(oid)
                dirty.add(oid)
            if on_o <= ce:
                rec.below_c[oid] = area
                o.above_c.add(rid)
            if o_on <= ce:
                o.below_c[rid] = area
                rec.above_c.add(oid)
        return dirty

    def _top_hull_of(self, rec: _Rec):
        if rec.top_hull is None:
            face = _face_hull(rec.xz, _FACE_CYCLES.get(rec.high_bits))
            rec.top_hull = (
                _hull([rec.xz[k] for k in _BIT_INDICES[rec.high_bits]]) if face is None else face
            )
        return rec.top_hull

    def _support_one(self, rec: _Rec) -> None:
        """`support.check_support`'s per-object body, for one object."""
        bottom_hull = rec.bottom_hull
        bottom_area = rec.bottom_area
        degenerate = bottom_area < DEGENERATE_AREA_M2

        candidates = (
            [("container_floor", self._floor_hull, rec.inside_floor)] if rec.floored else []
        )
        idx = self._index
        for sid in sorted(rec.below, key=idx.__getitem__):
            candidates.append((sid, self._top_hull_of(self._rec[sid]), False))

        names: list[str] = []
        patch_vertices: list = []
        covered = 0.0
        point_supported = [False] * len(rec.bottom_pts) if degenerate else []
        for name, top_hull, uncut in candidates:
            patch = bottom_hull if uncut else _clip(bottom_hull, top_hull)
            if not patch:
                continue
            names.append(name)
            area = bottom_area if patch is bottom_hull else _poly_area(patch)
            patch_vertices.extend(patch)
            covered += area
            if degenerate:
                for k, p in enumerate(rec.bottom_pts):
                    if not point_supported[k] and _signed_dist(p, top_hull) >= -TOUCH_TOL_M:
                        point_supported[k] = True

        if degenerate:
            support_ratio = (
                sum(point_supported) / len(point_supported) if point_supported else 0.0
            )
        else:
            support_ratio = min(1.0, covered / bottom_area)

        if len(names) == 1 and patch_vertices == bottom_hull:
            support_polygon = bottom_hull
        else:
            support_polygon = _hull(patch_vertices)
        if support_polygon:
            margin = _signed_dist(rec.center_xz, support_polygon)
            if -TOUCH_TOL_M < margin < 0.0:
                margin = 0.0
        else:
            margin = FLOATING_MARGIN_SENTINEL_M

        floating = support_ratio < self.floating_threshold
        unstable = margin < 0
        oid = rec.obj.id
        com_xz = [rec.center_xz[0], rec.center_xz[1]]
        sup = {
            "supporting_objects": names,
            "support_ratio": support_ratio,
            "floating": floating,
            "unstable": unstable,
            "supported_by_unstable": False,
            "floating_entry": None,
            "unstable_entry": None,
        }
        if floating:
            sup["floating_entry"] = {
                "type": "UNSUPPORTED_OBJECT",
                "object": oid,
                "support_ratio": support_ratio,
                "center_of_mass_projection": com_xz,
                "severity": _clamp01(1.0 - support_ratio / self.floating_threshold),
            }
        elif unstable:
            sup["unstable_entry"] = {
                "type": "UNSTABLE_STACK",
                "object": oid,
                "stability_margin_m": margin,
                "support_ratio": support_ratio,
                "supporting_objects": list(names),
                "center_of_mass_projection": com_xz,
                "contact_polygon": [list(p) for p in support_polygon],
            }
        rec.sup = sup

    def _refresh_support(self, dirty: set[str]) -> None:
        """Re-derive support for `dirty`, then propagate the chain-instability
        flag up through everything resting (transitively) on them. Nothing
        below a changed object can be affected, so nothing below is touched."""
        if not dirty:
            return
        recs = self._rec
        for oid in sorted(dirty, key=lambda i: recs[i].lo[1]):
            self._support_one(recs[oid])

        affected = set(dirty)
        stack = list(dirty)
        while stack:
            for up in recs[stack.pop()].above:
                if up not in affected:
                    affected.add(up)
                    stack.append(up)
        for oid in sorted(affected, key=lambda i: recs[i].lo[1]):
            sup = recs[oid].sup
            sup["supported_by_unstable"] = any(
                recs[s].sup["floating"]
                or recs[s].sup["unstable"]
                or recs[s].sup["supported_by_unstable"]
                for s in sup["supporting_objects"]
                if s != "container_floor"
            )
        self._con_cache = None

    def _exact_constraints(self) -> tuple[list[dict], list[dict]]:
        """validator.py's constraint block, replicating `check_constraints`.

        The transitive-load pass is O(n + edges) and only runs at all when some
        object is fragile / heavy / cannot_support_weight -- with no such flag
        anywhere in the scene, `load` is dead code in the original too and the
        per-object orientation entries (cached at commit) are the whole answer.
        """
        if self._con_cache is not None:
            return self._con_cache

        ids = self._ids
        n = len(ids)
        recs = [self._rec[oid] for oid in ids]
        violations: list[dict] = []
        warnings: list[dict] = []

        needs_load = any(
            r.obj.constraints.fragile
            or r.obj.constraints.cannot_support_weight
            or r.obj.constraints.heavy
            for r in recs
        )
        if not needs_load:
            for r in recs:
                violations.extend(r.orient)
            self._con_cache = (violations, warnings)
            return self._con_cache

        idx = self._index
        masses = [float(r.obj.mass_kg) for r in recs]
        direct_weight = [0.0] * n
        resting_on_direct: list[list[int]] = [[] for _ in range(n)]
        supporters: list[list[tuple[int, float]]] = [[] for _ in range(n)]
        total_support_area = [0.0] * n
        # resting_pairs' own order: top index ascending, then bottom ascending.
        for t in range(n):
            for sid in sorted(recs[t].below_c, key=idx.__getitem__):
                b = idx[sid]
                area = recs[t].below_c[sid]
                direct_weight[b] += masses[t]
                resting_on_direct[t].append(b)
                supporters[t].append((b, area))
                total_support_area[t] += area

        load = list(masses)
        for y in np.argsort(-np.array([r.lo[1] for r in recs])).tolist():
            area_total = total_support_area[y]
            if area_total <= 0.0:
                continue
            for x, area in supporters[y]:
                load[x] += load[y] * (area / area_total)
        supported_weight = [ld - m for ld, m in zip(load, masses)]

        def load_path(start: int) -> list[str]:
            path = [ids[start]]
            seen = {start}
            cur = start
            while supporters[cur]:
                nxt, _ = max(supporters[cur], key=lambda t: t[1])
                if nxt in seen:
                    break
                path.append(ids[nxt])
                seen.add(nxt)
                cur = nxt
            return path

        heavy_idx = [j for j, r in enumerate(recs) if r.obj.constraints.heavy]
        for i, r in enumerate(recs):
            c = r.obj.constraints
            violations.extend(r.orient)
            if c.cannot_support_weight and supported_weight[i] > 0:
                weight = float(supported_weight[i])
                violations.append({
                    "type": "FRAGILE_OBJECT_OVERLOADED",
                    "object": ids[i],
                    "supported_weight_kg": weight,
                    "direct_weight_kg": float(direct_weight[i]),
                    "severity": _clamp01(weight / max(0.1, masses[i])),
                })
        for i, r in enumerate(recs):
            c = r.obj.constraints
            if c.fragile:
                adjacent_heavy = any(
                    _adjacent_heavy(recs[i], recs[j]) for j in heavy_idx if j != i
                )
                if supported_weight[i] > 0 or adjacent_heavy:
                    warnings.append({
                        "type": "FRAGILE_LOAD",
                        "object": ids[i],
                        "supported_weight_kg": float(supported_weight[i]),
                        "direct_weight_kg": float(direct_weight[i]),
                        "adjacent_heavy": adjacent_heavy,
                    })
            if c.heavy and resting_on_direct[i]:
                warnings.append({
                    "type": "HEAVY_ON_TOP",
                    "object": ids[i],
                    "resting_on": [ids[b] for b in resting_on_direct[i]],
                    "load_path": load_path(i),
                })

        self._con_cache = (violations, warnings)
        return self._con_cache

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _commit(self, obj: Object) -> None:
        """Back-compat shim for the old private name; `commit` is the entry
        point (it took an already-built OBB, which is now derived internally)."""
        self.commit(obj)

    def _ensure_arrays(self) -> None:
        """Rebuild the legacy (k, ...) numpy caches the approximate path uses."""
        if not self._arrays_dirty:
            return
        recs = [self._rec[oid] for oid in self._ids]
        if recs:
            self._vertices = np.array([r.verts for r in recs])
            self._aabb_min = np.array([r.lo for r in recs])
            self._aabb_max = np.array([r.hi for r in recs])
        else:
            self._vertices = np.zeros((0, 8, 3))
            self._aabb_min = np.zeros((0, 3))
            self._aabb_max = np.zeros((0, 3))
        self._arrays_dirty = False

    def _evaluate(self, obj: Object) -> tuple[dict, OBB | None]:
        self._ensure_arrays()
        if obj.id in self._index:
            return _malformed(obj.id, ValueError(f"duplicate object id: {obj.id!r}")), None
        try:
            obb = obb_from(obj)
        except ValueError as e:
            return _malformed(obj.id, e), None

        violations: list[dict] = []
        warnings: list[dict] = []
        verts = obb_vertices(obb)
        new_min, new_max = verts.min(axis=0), verts.max(axis=0)

        v, w = self._containment_check(obj, obb)
        if v is not None:
            violations.append(v)
        if w is not None:
            warnings.append(w)

        cv, cw = self._collision_checks(obj, obb, new_min, new_max)
        violations.extend(cv)
        warnings.extend(cw)

        support_ratio, floating, stability_warning, supporters = self._support_check(
            obj, obb, verts
        )
        if floating:
            severity = _clamp01(1.0 - support_ratio / self.floating_threshold)
            violations.append(
                {
                    "type": "UNSUPPORTED_OBJECT",
                    "object": obj.id,
                    "support_ratio": support_ratio,
                    "severity": severity,
                }
            )
        elif stability_warning is not None:
            warnings.append(stability_warning)

        for sid in supporters:
            supporter_obj = self._rec[sid].obj
            # Direct load only: the new object's own mass on each supporter.
            # (The full validator propagates load transitively down a stack.)
            weight = obj.mass_kg
            if supporter_obj.constraints.cannot_support_weight:
                denom = max(0.1, supporter_obj.mass_kg)
                violations.append(
                    {
                        "type": "FRAGILE_OBJECT_OVERLOADED",
                        "object": sid,
                        "supported_weight_kg": weight,
                        "direct_weight_kg": weight,
                        "severity": _clamp01(weight / denom),
                    }
                )
            if supporter_obj.constraints.fragile:
                warnings.append(
                    {
                        "type": "FRAGILE_LOAD",
                        "object": sid,
                        "supported_weight_kg": weight,
                        "direct_weight_kg": weight,
                        "adjacent_heavy": bool(obj.constraints.heavy),
                    }
                )

        if obj.constraints.heavy and supporters:
            warnings.append(
                {"type": "HEAVY_ON_TOP", "object": obj.id, "resting_on": list(supporters)}
            )

        # The new object as a SUPPORTER. `_support_check` only ever looks down,
        # so a fragile pedestal slid in under an already-committed load used to
        # report clean while `validate_layout` flagged it overloaded.
        loaded, loaded_mass = self._objects_resting_on(new_min, new_max)
        if loaded and obj.constraints.cannot_support_weight:
            violations.append(
                {
                    "type": "FRAGILE_OBJECT_OVERLOADED",
                    "object": obj.id,
                    "supported_weight_kg": loaded_mass,
                    "direct_weight_kg": loaded_mass,
                    "severity": _clamp01(loaded_mass / max(0.1, obj.mass_kg)),
                }
            )

        if obj.constraints.fragile:
            own_rect = (float(new_min[0]), float(new_max[0]), float(new_min[2]), float(new_max[2]))
            own_y = (float(new_min[1]), float(new_max[1]))
            adjacent_heavy = any(
                self._rec[oid].obj.constraints.heavy
                and _rects_overlap(own_rect, self._xz_rect(i))
                and self._y_ranges_touch(own_y, i)
                for i, oid in enumerate(self._ids)
            )
            if loaded_mass > 0.0 or adjacent_heavy:
                warnings.append(
                    {
                        "type": "FRAGILE_LOAD",
                        "object": obj.id,
                        "supported_weight_kg": loaded_mass,
                        "direct_weight_kg": loaded_mass,
                        "adjacent_heavy": adjacent_heavy,
                    }
                )

        violations.extend(self._orientation_violations(obj, obb))

        violations.sort(key=_sort_key)
        warnings.sort(key=_sort_key)
        severity_sum = sum(min(1.0, v.get("severity", 1.0)) for v in violations)
        result = {
            "valid": len(violations) == 0,
            "score": max(0.0, 1.0 - severity_sum),
            "violations": violations,
            "warnings": warnings,
        }
        return result, obb

    def _objects_resting_on(self, new_min: np.ndarray, new_max: np.ndarray) -> tuple[list[str], float]:
        """Committed objects that come to rest ON a candidate with this AABB
        (bottom within `contact_eps` of the candidate's top, footprints
        overlapping), and their total direct mass. The mirror image of
        `_support_check`."""
        if not self._ids:
            return [], 0.0
        top_y = float(new_max[1])
        own_rect: Rect = (
            float(new_min[0]), float(new_max[0]), float(new_min[2]), float(new_max[2])
        )
        ids: list[str] = []
        mass = 0.0
        for idx in np.nonzero(np.abs(self._aabb_min[:, 1] - top_y) <= self.contact_eps)[0]:
            i = int(idx)
            if _rects_overlap(own_rect, self._xz_rect(i)):
                other = self._rec[self._ids[i]].obj
                ids.append(other.id)
                mass += other.mass_kg
        return ids, mass

    def _y_ranges_touch(self, own_y: tuple[float, float], idx: int) -> bool:
        """`constraints._adjacent_at_same_height`'s height gate, cached-array form."""
        tol = CONSTRAINT_CONTACT_EPS_M
        return (
            own_y[0] - tol <= float(self._aabb_max[idx, 1])
            and float(self._aabb_min[idx, 1]) - tol <= own_y[1]
        )

    def _xz_rect(self, idx: int) -> Rect:
        return (
            float(self._aabb_min[idx, 0]),
            float(self._aabb_max[idx, 0]),
            float(self._aabb_min[idx, 2]),
            float(self._aabb_max[idx, 2]),
        )

    def _containment_check(self, obj: Object, obb: OBB) -> tuple[dict | None, dict | None]:
        res = check_containment(self._container_obb, obb, epsilon=self.epsilon)
        if res.contained:
            return None, None
        raw_depth = res.penetration_depth_m
        # Per-wall allowance along that wall's axis (same rule as validator.py).
        if obj.rigidity == "rigid":
            effective = dict(res.per_wall_depth_m)
        else:
            effective = {}
            for wall, depth in res.per_wall_depth_m.items():
                axis = self._container_obb.axes[:, _WALL_AXIS_INDEX[wall[-1]]]
                allowance = container_wall_allowance_m(obj, axis_projected_extent_m(obb, axis))
                effective[wall] = max(0.0, depth - allowance)
        effective_depth = max(effective.values(), default=0.0)
        if effective_depth <= 0.0:
            if raw_depth > 0.0:
                return None, {
                    "type": "SOFT_COMPRESSION",
                    "object": obj.id,
                    "raw_penetration_depth_m": raw_depth,
                    "compressed_depth_m": raw_depth,
                    "walls": sorted(res.per_wall_depth_m),
                }
            return None, None
        severity = _clamp01(effective_depth / self._container_denom)
        return {
            "type": "CONTAINER_PENETRATION",
            "object": obj.id,
            "penetration_depth_m": effective_depth,
            "raw_penetration_depth_m": raw_depth,
            "compressed_depth_m": raw_depth - effective_depth,
            "violated_walls": sorted(w for w, d in effective.items() if d > 0.0),
            "per_wall_depth_m": {w: d for w, d in sorted(effective.items()) if d > 0.0},
            "penetrating_vertices": [_vec3(v) for v in res.penetrating_vertices],
            "severity": severity,
        }, None

    def _collision_checks(
        self, obj: Object, obb: OBB, new_min: np.ndarray, new_max: np.ndarray
    ) -> tuple[list[dict], list[dict]]:
        violations: list[dict] = []
        warnings: list[dict] = []
        if not self._ids:
            return violations, warnings

        # Vectorized O(k) AABB prefilter (mirrors collision.aabb_overlap, no
        # epsilon slack -- check_collision below re-applies its own epsilon
        # -aware margin on survivors).
        mask = np.all((self._aabb_max >= new_min) & (new_max >= self._aabb_min), axis=1)
        for idx in np.nonzero(mask)[0]:
            other_id = self._ids[idx]
            other_obj = self._rec[other_id].obj
            other_obb = self._rec[other_id].obb
            result = check_collision(obb, other_obb, epsilon=self.epsilon)
            if not result.colliding:
                continue
            raw_depth = result.penetration_depth_m
            if obj.rigidity == "rigid" and other_obj.rigidity == "rigid":
                allowance = 0.0
            else:
                allowance = combined_collision_allowance_m(
                    obj, other_obj, result.axis, obb, other_obb
                )
            effective_depth = max(0.0, raw_depth - allowance)
            pair_ids = sorted([obj.id, other_id])
            if effective_depth <= 0.0:
                if raw_depth > 0.0:
                    warnings.append(
                        {
                            "type": "SOFT_COMPRESSION",
                            "objects": pair_ids,
                            "raw_penetration_depth_m": raw_depth,
                            "compressed_depth_m": raw_depth,
                            "contact_point": _vec3(result.contact_point),
                        }
                    )
                continue
            denom = max(1e-9, min(min(obj.dimensions), min(other_obj.dimensions)))
            violations.append(
                {
                    "type": "OBJECT_COLLISION",
                    "objects": pair_ids,
                    "penetration_depth_m": effective_depth,
                    "raw_penetration_depth_m": raw_depth,
                    "compressed_depth_m": raw_depth - effective_depth,
                    "contact_point": _vec3(result.contact_point),
                    "axis": _vec3(result.axis),
                    "severity": _clamp01(effective_depth / denom),
                }
            )
        return violations, warnings

    def _support_check(
        self, obj: Object, obb: OBB, verts: np.ndarray
    ) -> tuple[float, bool, dict | None, list[str]]:
        bottom_y = float(verts[:, 1].min())
        own_rect: Rect = (
            float(verts[:, 0].min()),
            float(verts[:, 0].max()),
            float(verts[:, 2].min()),
            float(verts[:, 2].max()),
        )
        own_area = _rect_area(own_rect)

        support_rects: list[Rect] = []
        supporters: list[str] = []

        if abs(bottom_y - self._floor_y) <= self.contact_eps:
            clipped = _rect_intersection(own_rect, self._floor_rect)
            if clipped is not None:
                support_rects.append(clipped)

        if self._ids:
            top_y = self._aabb_max[:, 1]
            for idx in np.nonzero(np.abs(top_y - bottom_y) <= self.contact_eps)[0]:
                clipped = _rect_intersection(own_rect, self._xz_rect(idx))
                if clipped is not None:
                    support_rects.append(clipped)
                    supporters.append(self._ids[idx])

        covered = sum(_rect_area(r) for r in support_rects)
        support_ratio = 0.0 if own_area <= 0.0 else min(1.0, covered / own_area)
        floating = support_ratio < self.floating_threshold

        stability_warning = None
        if not floating and support_rects:
            merged = _merge_rect(support_rects)
            margin = _signed_dist_to_rect(float(obb.center[0]), float(obb.center[2]), merged)
            if margin < 0:
                stability_warning = {
                    "type": "UNSTABLE_STACK",
                    "object": obj.id,
                    "stability_margin_m": margin,
                    "center_of_mass_projection": [float(obb.center[0]), float(obb.center[2])],
                }
        return support_ratio, floating, stability_warning, supporters

    def _orientation_violations(self, obj: Object, obb: OBB) -> list[dict]:
        """Replicates constraints.py's ~10 lines of angle math for
        keep_upright/orientation_lock (see module docstring "Divergences")."""
        c = obj.constraints
        local_up = obb.axes[:, 1]
        cos = np.clip(np.dot(local_up, WORLD_UP) / np.linalg.norm(local_up), -1.0, 1.0)
        tilt = math.degrees(math.acos(cos))
        tol = DEFAULT_ANGLE_TOL_DEG
        denom = max(1e-9, 90.0 - tol)

        out: list[dict] = []

        def _entry(type_: str, tilt_used: float, extra: dict | None = None) -> dict:
            entry = {
                "type": type_,
                "object": obj.id,
                "severity": _clamp01((tilt_used - tol) / denom),
                "tilt_deg": tilt_used,
                "tolerance_deg": tol,
            }
            if extra:
                entry.update(extra)
            return entry

        if c.keep_upright and tilt > tol:
            out.append(_entry("LIQUID_NOT_UPRIGHT", tilt))

        lock = c.orientation_lock
        if lock == "this_side_up":
            if tilt > tol:
                out.append(_entry("INVALID_ORIENTATION", tilt, {"lock": lock}))
        elif lock == "flat_only":
            angle_to_axis = min(tilt, 180.0 - tilt)
            if angle_to_axis > tol:
                out.append(_entry("INVALID_ORIENTATION", angle_to_axis, {"lock": lock}))
        elif lock == "horizontal":
            angle_to_plane = abs(90.0 - tilt)
            if angle_to_plane > tol:
                out.append(_entry("INVALID_ORIENTATION", angle_to_plane, {"lock": lock}))

        return out
