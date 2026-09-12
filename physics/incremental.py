"""Incremental, per-object placement validation for the packing solver's inner loop.

Problem this solves: `validator.validate_layout` re-validates an ENTIRE scene
(O(k^2)) every time the solver asks "I have k objects placed, can I add this
one here?". `PlacementValidator` instead keeps a small cache of the k already
-committed objects and, on each query, only evaluates the ONE new object
against that cache: O(k) per call (a vectorized AABB prefilter over the
cached (k,3) min/max arrays, then narrow-phase only on survivors), not O(k^2)
over the whole scene, and not O(k) SceneGeometry-style precompute either --
`physics.scene_geometry.precompute` rebuilds everything from scratch, this
module grows its own arrays in place via `np.vstack` on `place`/shrinks them
on `remove` instead.

Contract
--------
`try_place(obj) -> dict` and `place(obj) -> dict` both return a dict shaped
EXACTLY like `validate_layout`'s result -- `{"valid", "score", "violations",
"warnings"}`, using the same violation/warning types, dict shapes, and
severity formulas (see `physics.validator` module docstring) -- but the
violations/warnings describe ONLY `obj` against the currently-committed
state, not the whole scene. `score` uses `n = 1` (one object per call):
`score = max(0, 1 - sum(min(1, severity) for v in violations))`. Never raises:
malformed input (bad geometry, from `obb_from`'s ValueError, or a duplicate
id) becomes a `MALFORMED_GEOMETRY` violation, exactly like `validate_layout`.

What is NOT re-checked
-----------------------
Previously committed objects' mutual validity (their pairwise collisions,
containment, support, orientation) is NOT re-verified on every query -- that
was already checked when each was placed (via `place`/`try_place`), and
revalidating everyone on every call is exactly the O(k^2)-per-call cost this
module exists to avoid. This is the caller's responsibility to have upheld
(only place objects whose own `try_place` was valid, or that you explicitly
accept via `force=True`).

The one documented exception: placing a new object ON TOP of an existing one
can change that existing object's `FRAGILE_OBJECT_OVERLOADED` status (a
laptop that was fine alone becomes overloaded once something rests on it).
This IS handled here -- `try_place`/`place` check the NEW object's direct
supporters' `cannot_support_weight` flag and emit `FRAGILE_OBJECT_OVERLOADED`
for the SUPPORTER's id when it applies. Nothing else about already-committed
objects is re-derived (e.g. a supporter's own `FRAGILE_LOAD`/`HEAVY_ON_TOP`
warnings are not recomputed, since those never invalidate a scene and are
cheap to catch in the final authoritative pass below).

Recommendation: once the solver is done, run
`validate_layout(placement_validator.to_scene())` ONE time as the
authoritative full check -- this module optimizes the hot inner loop, it does
not replace the full pipeline's guarantees.

Divergences from `physics.validator` accepted for this module
----------------------------------------------------------------
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
`try_place`/`place`: O(k) -- the collision scan is a vectorized O(k) AABB
prefilter (one numpy comparison over the cached (k,3) arrays) followed by
O(1) SAT (`check_collision`) only on the surviving candidates; support and
fragile/heavy checks are each O(k) (one vectorized height compare, then a
Python loop only over contact survivors); containment and orientation are
O(1). `place`/`remove` are O(k) (an `np.vstack`/boolean-mask rebuild of the
cached arrays) -- fine up to the hackathon-scale k<=50 the docstring targets;
a persistent spatial index would be the upgrade path beyond that.

Cache
-----
Per committed object: its `Object` and `OBB` (kept in parallel Python lists
/dicts -- dataclasses don't vectorize) plus its world vertices and AABB
min/max (kept as growing `(k,8,3)`/`(k,3)` numpy arrays, appended via
`np.vstack` on `place`). `remove` rebuilds the arrays via a boolean mask
rather than a full recompute.
"""
from __future__ import annotations

import math

import numpy as np

from physics.collision import check_collision
from physics.compressibility import (
    axis_projected_extent_m,
    combined_collision_allowance_m,
    container_wall_allowance_m,
)
from physics.containment import check_containment
from physics.geometry import OBB, obb_from, obb_vertices
from physics.schema import Container, Object, Scene

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

        self._ids: list[str] = []
        self._index: dict[str, int] = {}
        self._objects: dict[str, Object] = {}
        self._obbs: list[OBB] = []
        self._vertices = np.zeros((0, 8, 3))
        self._aabb_min = np.zeros((0, 3))
        self._aabb_max = np.zeros((0, 3))

    @property
    def placed_ids(self) -> list[str]:
        return list(self._ids)

    def to_scene(self) -> Scene:
        return Scene(container=self.container, objects=[self._objects[oid] for oid in self._ids])

    def try_place(self, obj: Object) -> dict:
        result, _ = self._evaluate(obj)
        return result

    def place(self, obj: Object, *, force: bool = False) -> dict:
        result, obb = self._evaluate(obj)
        if obb is not None and (result["valid"] or force):
            self._commit(obj, obb)
        return result

    def remove(self, object_id: str) -> None:
        idx = self._index.pop(object_id)
        mask = np.ones(len(self._ids), dtype=bool)
        mask[idx] = False
        del self._ids[idx]
        del self._objects[object_id]
        del self._obbs[idx]
        self._vertices = self._vertices[mask]
        self._aabb_min = self._aabb_min[mask]
        self._aabb_max = self._aabb_max[mask]
        self._index = {oid: i for i, oid in enumerate(self._ids)}

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
    # internals
    # ------------------------------------------------------------------

    def _commit(self, obj: Object, obb: OBB) -> None:
        self._index[obj.id] = len(self._ids)
        self._ids.append(obj.id)
        self._objects[obj.id] = obj
        self._obbs.append(obb)
        verts = obb_vertices(obb)
        self._vertices = np.vstack([self._vertices, verts[None, ...]])
        self._aabb_min = np.vstack([self._aabb_min, verts.min(axis=0)[None, :]])
        self._aabb_max = np.vstack([self._aabb_max, verts.max(axis=0)[None, :]])

    def _evaluate(self, obj: Object) -> tuple[dict, OBB | None]:
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
            supporter_obj = self._objects[sid]
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

        if obj.constraints.fragile:
            own_rect = (float(new_min[0]), float(new_max[0]), float(new_min[2]), float(new_max[2]))
            adjacent_heavy = any(
                self._objects[oid].constraints.heavy and _rects_overlap(own_rect, self._xz_rect(i))
                for i, oid in enumerate(self._ids)
            )
            if adjacent_heavy:
                warnings.append(
                    {
                        "type": "FRAGILE_LOAD",
                        "object": obj.id,
                        "supported_weight_kg": 0.0,
                        "adjacent_heavy": True,
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
            other_obj = self._objects[other_id]
            result = check_collision(obb, self._obbs[idx], epsilon=self.epsilon)
            if not result.colliding:
                continue
            raw_depth = result.penetration_depth_m
            if obj.rigidity == "rigid" and other_obj.rigidity == "rigid":
                allowance = 0.0
            else:
                allowance = combined_collision_allowance_m(
                    obj, other_obj, result.axis, obb, self._obbs[idx]
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
