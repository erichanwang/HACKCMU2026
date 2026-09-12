"""Top-level physics validation entry point: `validate_layout(scene) -> dict`.

One call runs the whole deterministic pipeline on a candidate layout:

    precompute (physics.scene_geometry)    one pass: OBBs, vertices, AABBs
      -> containment  (vectorized, per-wall depths)
      -> collision    (AABB broad phase + batched 15-axis SAT)
      -> support      (exact contact polygons, static stability, chains)
      -> constraints  (travel metadata, transitive load)
      -> metrics      (COM, fill, clearances -- solver objective terms)

This is the only function the packing-solver / renderer teammates need to
call directly. For a solver inner loop ("can I add this one object?") use
`physics.incremental.PlacementValidator` and call this once at the end.

Malformed-input contract
-------------------------
Never raises. `precompute` validates every object (finite, positive dims;
finite position; non-zero finite quaternion; unique ids). Any failure returns
immediately: `{"valid": False, "score": 0.0, "violations": [{"type":
"MALFORMED_GEOMETRY", "object": <id or None>, "detail": <message>}],
"warnings": [], "metrics": {}}`.

Output shape
------------
    {
      "valid": bool,        # True iff `violations` is empty
      "score": float,       # 1.0 clean -> lower as violations pile up/worsen
      "violations": [...],  # hard failures (physically impossible / forbidden)
      "warnings": [...],    # soft / informational; never affect `valid` or `score`
      "metrics": {...},     # physics.metrics.scene_metrics: total_mass_kg,
                            # center_of_mass, com_offset_m, fill_ratio, per_object{...}
    }

Violation types: OBJECT_COLLISION, CONTAINER_PENETRATION, UNSUPPORTED_OBJECT,
FRAGILE_OBJECT_OVERLOADED, LIQUID_NOT_UPRIGHT, INVALID_ORIENTATION,
MALFORMED_GEOMETRY. Warning types: UNSTABLE_STACK, UNSTABLE_SUPPORT_CHAIN,
SOFT_COMPRESSION, FRAGILE_LOAD, HEAVY_ON_TOP.

Every entry carries renderer-ready geometry where it exists: collisions have
`contact_point` and `axis` (MTV direction), container penetrations have
`penetrating_vertices` and `per_wall_depth_m`, stability warnings have the
`contact_polygon` (support hull, [x, z] pairs) and `center_of_mass_projection`.

Hard-fail vs warning policy
----------------------------
- OBJECT_COLLISION / CONTAINER_PENETRATION are hard fails -- after subtracting
  the compressibility allowance (`physics.compressibility`, from each object's
  `rigidity` / `compressibility_k`). Rigid objects have zero allowance, so
  rigid-only scenes behave exactly as plain geometry. If the allowance fully
  absorbs the raw overlap, the pair (or wall) becomes a SOFT_COMPRESSION
  warning; if overlap remains, it is a violation whose depth and severity are
  the *excess* only. Container walls are handled per wall: each violated
  wall's depth is reduced by the object's allowance along that wall's axis.
  Both the warning and the residual violation report `raw_penetration_depth_m`
  (geometric overlap) and `compressed_depth_m` (the part absorbed as squish;
  equals the raw depth when fully absorbed) so the renderer can draw the
  compression and any excess separately.
- UNSUPPORTED_OBJECT (support_ratio < floating_threshold) is a hard fail.
- UNSTABLE_STACK (supported, but COM outside the support polygon) is a warning:
  precarious, not impossible (no friction/dynamics modeled -- see support.py).
  UNSTABLE_SUPPORT_CHAIN flags an object that is itself fine but rests on
  something floating/unstable.
- Constraint passthrough: LIQUID_NOT_UPRIGHT / INVALID_ORIENTATION /
  FRAGILE_OBJECT_OVERLOADED stay violations; FRAGILE_LOAD / HEAVY_ON_TOP stay
  warnings. `supported_weight_kg` is the TRANSITIVE load (a shoe on a bag on
  a laptop loads the laptop); `direct_weight_kg` is also reported.

Severity heuristics (each in [0, 1]; used only for `score`)
------------------------------------------------------------
- OBJECT_COLLISION: depth / min(thinnest extent of A, thinnest extent of B).
- CONTAINER_PENETRATION: depth / smallest container half-extent.
- UNSUPPORTED_OBJECT: 1 - support_ratio / floating_threshold.
- FRAGILE_OBJECT_OVERLOADED: supported_weight_kg / max(0.1, own mass_kg).
- LIQUID_NOT_UPRIGHT / INVALID_ORIENTATION: (tilt - tol) / (90 - tol).
- MALFORMED_GEOMETRY: none (score forced to 0.0).

Score: `max(0, 1 - sum(min(1, severity)) / max(1, n_objects))`. Warnings never
affect it. Monotonic in the number and severity of violations.

Determinism
------------
No randomness anywhere. `violations` and `warnings` are each sorted by
`(type, object_id)` (a collision pair uses its lexicographically smaller id),
so repeated calls on the same scene are byte-identical under `json.dumps`.

Complexity / performance
------------------------
O(n) precompute + O(n^2) vectorized broad phase + batched SAT on survivors;
support and constraints are O(n^2) topology + small Python loops over
contacts. Measured (tests/benchmark_validator.py, single machine): ~1.1 ms
for 20 sparse objects, ~2.1 ms for 20 objects with 55 real collisions, ~3 ms
for a dense 40-object scene; `PlacementValidator.try_place` ~0.2 ms per
candidate at k=20. v1 took ~11.6 ms for 20 sparse objects. The packing
solver can call this hundreds of times per second, and the incremental API
thousands.
"""
from __future__ import annotations

import numpy as np

from physics.collision import collide_scene
from physics.compressibility import (
    axis_projected_extent_m,
    combined_collision_allowance_m,
    container_wall_allowance_m,
)
from physics.constraints import check_constraints
from physics.containment import check_scene_containment
from physics.metrics import scene_metrics
from physics.scene_geometry import MalformedSceneError, precompute
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


def _malformed(object_id, exc: Exception) -> dict:
    return {
        "valid": False,
        "score": 0.0,
        "violations": [{"type": "MALFORMED_GEOMETRY", "object": object_id, "detail": str(exc)}],
        "warnings": [],
        "metrics": {},
    }


def validate_layout(scene: Scene, *, floating_threshold: float = DEFAULT_FLOATING_THRESHOLD) -> dict:
    """Run the full physics validation pipeline on `scene`. See module docstring."""
    try:
        geom = precompute(scene)
    except MalformedSceneError as e:
        return _malformed(e.object_id, e)
    except ValueError as e:  # defensive: anything geometry.py raises that isn't tagged
        return _malformed(None, e)

    objs = geom.objects
    n = max(1, geom.n)
    violations: list[dict] = []
    warnings: list[dict] = []
    container = geom.container_obb

    # --- Containment (vectorized; per-wall compressibility) ---
    container_denom = max(1e-9, float(np.min(container.half_extents)))
    for res in check_scene_containment(scene, geom=geom):
        i = geom.index[res.object_id]
        obj = objs[i]
        raw_depth = res.penetration_depth_m
        if obj.rigidity == "rigid":
            effective = dict(res.per_wall_depth_m)
        else:
            effective = {}
            for wall, depth in res.per_wall_depth_m.items():
                axis = container.axes[:, _WALL_AXIS_INDEX[wall[-1]]]
                allowance = container_wall_allowance_m(obj, axis_projected_extent_m(geom.obbs[i], axis))
                effective[wall] = max(0.0, depth - allowance)
        effective_depth = max(effective.values(), default=0.0)
        if effective_depth <= 0.0:
            if raw_depth > 0.0:
                warnings.append({
                    "type": "SOFT_COMPRESSION",
                    "object": res.object_id,
                    "raw_penetration_depth_m": raw_depth,
                    "compressed_depth_m": raw_depth,
                    "walls": sorted(res.per_wall_depth_m),
                })
            continue
        violations.append({
            "type": "CONTAINER_PENETRATION",
            "object": res.object_id,
            "penetration_depth_m": effective_depth,
            "raw_penetration_depth_m": raw_depth,
            "compressed_depth_m": raw_depth - effective_depth,
            "violated_walls": sorted(w for w, d in effective.items() if d > 0.0),
            "per_wall_depth_m": {w: d for w, d in sorted(effective.items()) if d > 0.0},
            "penetrating_vertices": [_vec3(v) for v in res.penetrating_vertices],
            "severity": _clamp01(effective_depth / container_denom),
        })

    # --- Collisions (broad phase + batched SAT; only colliding pairs come back) ---
    for r in collide_scene(geom):
        i, j = geom.index[r.a_id], geom.index[r.b_id]
        a, b = objs[i], objs[j]
        raw_depth = r.penetration_depth_m
        if a.rigidity == "rigid" and b.rigidity == "rigid":
            allowance = 0.0
        else:
            allowance = combined_collision_allowance_m(a, b, r.axis, geom.obbs[i], geom.obbs[j])
        effective_depth = max(0.0, raw_depth - allowance)
        pair = sorted([a.id, b.id])
        if effective_depth <= 0.0:
            if raw_depth > 0.0:
                warnings.append({
                    "type": "SOFT_COMPRESSION",
                    "objects": pair,
                    "raw_penetration_depth_m": raw_depth,
                    "compressed_depth_m": raw_depth,
                    "contact_point": _vec3(r.contact_point),
                })
            continue
        denom = max(1e-9, min(min(a.dimensions), min(b.dimensions)))
        violations.append({
            "type": "OBJECT_COLLISION",
            "objects": pair,
            "penetration_depth_m": effective_depth,
            "raw_penetration_depth_m": raw_depth,
            "compressed_depth_m": raw_depth - effective_depth,
            "contact_point": _vec3(r.contact_point),
            "axis": _vec3(r.axis),
            "severity": _clamp01(effective_depth / denom),
        })

    # --- Support / static stability ---
    for s in check_support(scene, floating_threshold=floating_threshold, geom=geom):
        center = geom.centers[geom.index[s.object_id]]
        com_xz = [float(center[0]), float(center[2])]
        if s.floating:
            violations.append({
                "type": "UNSUPPORTED_OBJECT",
                "object": s.object_id,
                "support_ratio": s.support_ratio,
                "center_of_mass_projection": com_xz,
                "severity": _clamp01(1.0 - s.support_ratio / floating_threshold),
            })
        elif s.unstable:
            warnings.append({
                "type": "UNSTABLE_STACK",
                "object": s.object_id,
                "stability_margin_m": s.stability_margin_m,
                "support_ratio": s.support_ratio,
                "supporting_objects": list(s.supporting_objects),
                "center_of_mass_projection": com_xz,
                "contact_polygon": [list(p) for p in s.contact_polygon],
            })
        elif s.supported_by_unstable:
            warnings.append({
                "type": "UNSTABLE_SUPPORT_CHAIN",
                "object": s.object_id,
                "supporting_objects": list(s.supporting_objects),
            })

    # --- Travel constraints (transitive load) ---
    mass_by_id = {o.id: o.mass_kg for o in objs}
    c_violations, c_warnings = check_constraints(scene, geom=geom)
    for v in c_violations:
        entry = {"type": v.type, "object": v.object_id}
        entry.update(v.details)
        if v.type == "FRAGILE_OBJECT_OVERLOADED":
            weight = v.details.get("supported_weight_kg", 0.0)
            entry["severity"] = _clamp01(weight / max(0.1, mass_by_id.get(v.object_id, 0.1)))
        else:  # LIQUID_NOT_UPRIGHT / INVALID_ORIENTATION
            tilt = v.details.get("tilt_deg", 0.0)
            tol = v.details.get("tolerance_deg", 15.0)
            entry["severity"] = _clamp01((tilt - tol) / max(1e-9, 90.0 - tol))
        violations.append(entry)
    for w in c_warnings:
        entry = {"type": w.type, "object": w.object_id}
        entry.update(w.details)
        warnings.append(entry)

    violations.sort(key=_sort_key)
    warnings.sort(key=_sort_key)
    severity_sum = sum(min(1.0, v.get("severity", 1.0)) for v in violations)

    return {
        "valid": len(violations) == 0,
        "score": max(0.0, 1.0 - severity_sum / n),
        "violations": violations,
        "warnings": warnings,
        "metrics": scene_metrics(geom),
    }
