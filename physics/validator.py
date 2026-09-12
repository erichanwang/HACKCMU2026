"""Top-level physics validation entry point: `validate_layout(scene) -> dict`.

Integrates every sibling module into one call: `containment.check_scene_containment`,
`collision.check_collision` (all object pairs, pruned by `collision.aabb_overlap`
broad-phase), `support.check_support`, and `constraints.check_constraints`. This is
the only function the packing-solver / renderer teammates should call directly.

Malformed-input contract
-------------------------
This function never raises. Before running any check it validates scene geometry
up front (`check_no_duplicate_ids` + `obb_from` on the container and every object).
If that raises `ValueError` (NaN/non-finite dims or position, bad quaternion,
duplicate ids), it is caught and a `MALFORMED_GEOMETRY` violation is returned
immediately with `score=0.0`, `valid=False` -- the rest of the pipeline never
runs on data it can't trust.

Output shape
------------
    {
      "valid": bool,        # True iff `violations` is empty
      "score": float,       # 1.0 clean -> lower as violations pile up/worsen
      "violations": [...],  # hard failures, see below
      "warnings": [...],    # soft/informational, do not affect `valid`/`score`
    }

Violation types: OBJECT_COLLISION, CONTAINER_PENETRATION, UNSUPPORTED_OBJECT,
FRAGILE_OBJECT_OVERLOADED, LIQUID_NOT_UPRIGHT, INVALID_ORIENTATION,
MALFORMED_GEOMETRY. Warning types: UNSTABLE_STACK, FRAGILE_LOAD, HEAVY_ON_TOP,
SOFT_COMPRESSION (see compressibility policy below).

Hard-fail vs warning policy
----------------------------
- A completely unsupported object (`SupportResult.floating`, i.e.
  `support_ratio < floating_threshold`) is a hard-fail `UNSUPPORTED_OBJECT`
  violation -- an item with (near-)zero contact area under gravity is not a
  packable layout.
- An object that IS supported (`support_ratio >= floating_threshold`) but whose
  center of mass falls outside its support footprint (`SupportResult.unstable`)
  is only a `UNSTABLE_STACK` warning -- it is precariously balanced, not
  physically impossible, and `valid` can still be True for such a scene. This
  is a deliberate simplification (no friction/tip-over dynamics modeled here,
  see support.py) -- treat repeated UNSTABLE_STACK warnings as "worth a second
  look", not "reject".
- Collisions and container-wall penetrations are always hard fails (the
  geometry is physically impossible) -- EXCEPT that raw overlap/penetration is
  first reduced by a compressibility allowance (`physics.compressibility`,
  derived from each object's `rigidity`/`compressibility_k`) before that
  decision is made: if the allowance fully absorbs the raw overlap the pair
  becomes a `SOFT_COMPRESSION` warning instead of a violation (rigid objects
  get zero allowance, so this never changes behavior for rigid-only scenes);
  if some overlap remains past what compression can plausibly absorb, it's
  still a hard-fail OBJECT_COLLISION/CONTAINER_PENETRATION, but severity and
  `penetration_depth_m` use only that excess, not the raw depth.
  LIQUID_NOT_UPRIGHT / INVALID_ORIENTATION / FRAGILE_OBJECT_OVERLOADED
  passthrough from `check_constraints` stay hard fails (they are already
  violations there); FRAGILE_LOAD / HEAVY_ON_TOP stay warnings (already
  warnings there).

Severity heuristics (each in [0, 1], documented per type; used only for score)
--------------------------------------------------------------------------------
- OBJECT_COLLISION: penetration_depth_m / min(smallest full extent of A, smallest
  full extent of B) -- a penetration comparable to an object's own thinnest
  dimension is maximally severe.
- CONTAINER_PENETRATION: penetration_depth_m / smallest container half-extent.
- UNSUPPORTED_OBJECT: 1 - support_ratio / floating_threshold -- 1.0 for a fully
  floating object (support_ratio=0), tapering to 0 as support_ratio approaches
  the floating threshold from below.
- FRAGILE_OBJECT_OVERLOADED: supported_weight_kg / max(0.1, object's own mass_kg)
  -- weight piled on a fragile item relative to its own mass.
- LIQUID_NOT_UPRIGHT / INVALID_ORIENTATION: (tilt_deg - tolerance_deg) / (90 -
  tolerance_deg) -- 0 right at the tolerance boundary, 1.0 at a full 90 degree
  tilt (the worst case for these angle metrics).
- MALFORMED_GEOMETRY: no severity (short-circuits with score=0.0 regardless).

Score formula
--------------
`score = max(0, 1 - sum(min(1, severity) for v in violations) / max(1, n))`
where `n = len(scene.objects)`. Warnings never affect score. Monotonic:
adding a violation or increasing any severity can only lower or hold the score.

Determinism
------------
No randomness. `violations` and `warnings` are each sorted by `(type, object_id)`
(the pair's lexicographically-smaller id for OBJECT_COLLISION) before being
returned, so `json.dumps(result, sort_keys=False)` is byte-identical across
repeated calls on the same scene.

Complexity
-----------
O(n^2) overall, dominated by the all-pairs collision scan (n = object count):
each pair is pruned first by O(1) `aabb_overlap`, and only surviving pairs run
full O(1) SAT via `check_collision` -- so the O(n^2) factor is cheap per-pair
broad-phase work, not O(n^2) SAT. `check_scene_containment` is O(n),
`check_support` and `check_constraints` are each O(n^2) internally (same scale
as the collision scan, see their own docstrings) -- fine at hackathon scale
(5-20 objects); a spatial index would be the upgrade path beyond that.
"""
from __future__ import annotations

import numpy as np

from physics.collision import aabb_overlap, check_collision
from physics.compressibility import (
    axis_projected_extent_m,
    combined_collision_allowance_m,
    container_wall_allowance_m,
)
from physics.constraints import check_constraints
from physics.containment import check_no_duplicate_ids, check_scene_containment
from physics.geometry import obb_from
from physics.schema import Scene
from physics.support import check_support

# wall name suffix ("+x" / "-x" -> "x") -> container-local axis index
_WALL_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}

DEFAULT_FLOATING_THRESHOLD = 0.05


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


def _malformed(object_id, exc: ValueError) -> dict:
    return {
        "valid": False,
        "score": 0.0,
        "violations": [
            {"type": "MALFORMED_GEOMETRY", "object": object_id, "detail": str(exc)}
        ],
        "warnings": [],
    }


def validate_layout(scene: Scene) -> dict:
    """Run the full physics validation pipeline on `scene`. See module docstring."""
    # Step 1: malformed-input guard -- never let a bad scene raise past here.
    try:
        check_no_duplicate_ids(scene)
    except ValueError as e:
        return _malformed(None, e)

    try:
        container_obb = obb_from(scene.container)
    except ValueError as e:
        return _malformed(scene.container.id, e)

    obbs = {}
    try:
        for obj in scene.objects:
            obbs[obj.id] = obb_from(obj)
    except ValueError as e:
        return _malformed(obj.id, e)

    n = max(1, len(scene.objects))
    violations: list[dict] = []
    warnings: list[dict] = []

    # --- Containment ---
    objs = scene.objects
    obj_by_id = {o.id: o for o in objs}
    container_denom = max(1e-9, float(np.min(container_obb.half_extents)))
    for res in check_scene_containment(scene):
        raw_depth = res.penetration_depth_m
        allowance = 0.0
        if res.violated_walls:
            # check_containment only reports one scalar penetration_depth_m
            # (already the max across every violated vertex/axis), not a
            # per-wall breakdown, so there's no way to pick "the wall with
            # the largest raw penetration" from ContainmentResult alone
            # without touching containment.py (out of scope for this task).
            # Resolution: use the axis of the first violated wall in sorted
            # (deterministic) order as the stand-in axis for the object's
            # own compressible extent -- simple, deterministic, and the
            # `raw_depth` used below is still the true overall max.
            axis_idx = _WALL_AXIS_INDEX[sorted(res.violated_walls)[0][-1]]
            axis = container_obb.axes[:, axis_idx]
            obj = obj_by_id[res.object_id]
            extent = axis_projected_extent_m(obbs[res.object_id], axis)
            allowance = container_wall_allowance_m(obj, extent)
        effective_depth = max(0.0, raw_depth - allowance)
        if effective_depth <= 0.0:
            if raw_depth > 0.0:
                warnings.append(
                    {
                        "type": "SOFT_COMPRESSION",
                        "object": res.object_id,
                        "raw_penetration_depth_m": raw_depth,
                        "compressed_depth_m": raw_depth,
                    }
                )
            continue
        severity = _clamp01(effective_depth / container_denom)
        violations.append(
            {
                "type": "CONTAINER_PENETRATION",
                "object": res.object_id,
                "penetration_depth_m": effective_depth,
                "violated_walls": list(res.violated_walls),
                "severity": severity,
            }
        )

    # --- Collisions: all pairs, aabb_overlap broad-phase first (O(n^2) total). ---
    for i in range(len(objs)):
        for j in range(i + 1, len(objs)):
            a, b = objs[i], objs[j]
            oa, ob = obbs[a.id], obbs[b.id]
            if not aabb_overlap(oa, ob):
                continue
            result = check_collision(oa, ob)
            if result.colliding:
                raw_depth = result.penetration_depth_m
                allowance = combined_collision_allowance_m(a, b, result.axis, oa, ob)
                effective_depth = max(0.0, raw_depth - allowance)
                if effective_depth <= 0.0:
                    if raw_depth > 0.0:
                        warnings.append(
                            {
                                "type": "SOFT_COMPRESSION",
                                "objects": sorted([a.id, b.id]),
                                "raw_penetration_depth_m": raw_depth,
                                "compressed_depth_m": raw_depth,
                            }
                        )
                    continue
                denom = max(1e-9, min(min(a.dimensions), min(b.dimensions)))
                severity = _clamp01(effective_depth / denom)
                violations.append(
                    {
                        "type": "OBJECT_COLLISION",
                        "objects": sorted([a.id, b.id]),
                        "penetration_depth_m": effective_depth,
                        "contact_point": _vec3(result.contact_point),
                        "severity": severity,
                    }
                )

    # --- Support ---
    for r in check_support(scene, floating_threshold=DEFAULT_FLOATING_THRESHOLD):
        if r.floating:
            severity = _clamp01(1.0 - r.support_ratio / DEFAULT_FLOATING_THRESHOLD)
            violations.append(
                {
                    "type": "UNSUPPORTED_OBJECT",
                    "object": r.object_id,
                    "support_ratio": r.support_ratio,
                    "severity": severity,
                }
            )
        elif r.unstable:
            center = obbs[r.object_id].center
            warnings.append(
                {
                    "type": "UNSTABLE_STACK",
                    "object": r.object_id,
                    "stability_margin_m": r.stability_margin_m,
                    "center_of_mass_projection": [float(center[0]), float(center[2])],
                }
            )

    # --- Constraints (travel semantics) ---
    mass_by_id = {o.id: o.mass_kg for o in objs}
    c_violations, c_warnings = check_constraints(scene)
    for v in c_violations:
        if v.type == "FRAGILE_OBJECT_OVERLOADED":
            weight = v.details.get("supported_weight_kg", 0.0)
            denom = max(0.1, mass_by_id.get(v.object_id, 0.1))
            severity = _clamp01(weight / denom)
            violations.append(
                {
                    "type": v.type,
                    "object": v.object_id,
                    "supported_weight_kg": weight,
                    "severity": severity,
                }
            )
        else:  # LIQUID_NOT_UPRIGHT / INVALID_ORIENTATION
            tilt = v.details.get("tilt_deg", 0.0)
            tol = v.details.get("tolerance_deg", 15.0)
            denom = max(1e-9, 90.0 - tol)
            severity = _clamp01((tilt - tol) / denom)
            entry = {"type": v.type, "object": v.object_id, "severity": severity}
            entry.update(v.details)
            violations.append(entry)

    for w in c_warnings:
        entry = {"type": w.type, "object": w.object_id}
        entry.update(w.details)
        warnings.append(entry)

    violations.sort(key=_sort_key)
    warnings.sort(key=_sort_key)

    severity_sum = sum(min(1.0, v.get("severity", 1.0)) for v in violations)
    score = max(0.0, 1.0 - severity_sum / n)

    return {
        "valid": len(violations) == 0,
        "score": score,
        "violations": violations,
        "warnings": warnings,
    }
