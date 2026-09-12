"""JSON integration boundary for the physics layer.

Three jobs:
1. Round-trip `physics.schema` Scene/Object/Container to/from plain JSON-able
   dicts (`scene_to_dict` / `scene_from_dict`), and dump a validator result to
   JSON (`result_to_json`).
2. Adapt the iOS LiDAR scanner's shapes into `Object`s: `object_from_scanned_item`
   (the scanner's `ScannedItem`, centimetres, no pose) and `object_from_box_fit`
   (the scanner's internal `BoxFit`, metres, yaw-only world pose).
3. Adapt the packing solver's placement format: `apply_placements` /
   `validate` (both spelled `position`/`rotation` and `target_position`/
   `target_rotation` are used across the team's docs -- both are accepted).

See `examples/README.md` for concrete JSON and the exact CLI invocations.

An `Object` built by `object_from_scanned_item` has no real pose (the scanner
supplies none) -- callers pass a placeholder `position`/`rotation` (default:
origin/identity). That is a legitimate input to hand the packing *solver*
(which only cares about dimensions), but not a meaningful input to
`validate_layout`/`validate` until the solver (or `apply_placements`) has
given it a real pose.
"""
from __future__ import annotations

import json
import math
from dataclasses import replace
from typing import Any, Optional

import numpy as np

from physics.schema import Constraints, Container, Object, Scene
from physics.validator import validate_layout

IDENTITY_ROTATION = (0.0, 0.0, 0.0, 1.0)


def _floats(v) -> list[float]:
    return [float(c) for c in v]


def _rotation_or_identity(r) -> tuple[float, float, float, float]:
    if r is None:
        return IDENTITY_ROTATION
    return tuple(float(c) for c in r)


# --- Constraints ------------------------------------------------------------


def constraints_to_dict(c: Constraints) -> dict:
    return {
        "fragile": c.fragile,
        "keep_upright": c.keep_upright,
        "cannot_support_weight": c.cannot_support_weight,
        "heavy": c.heavy,
        "orientation_lock": c.orientation_lock,
    }


def constraints_from_dict(d: Optional[dict]) -> Constraints:
    d = d or {}
    return Constraints(
        fragile=bool(d.get("fragile", False)),
        keep_upright=bool(d.get("keep_upright", False)),
        cannot_support_weight=bool(d.get("cannot_support_weight", False)),
        heavy=bool(d.get("heavy", False)),
        orientation_lock=d.get("orientation_lock"),
    )


# --- Object / Container / Scene ---------------------------------------------


def object_to_dict(o: Object) -> dict:
    return {
        "id": o.id,
        "dimensions": _floats(o.dimensions),
        "position": _floats(o.position),
        "rotation": _floats(o.rotation),
        "mass_kg": float(o.mass_kg),
        "constraints": constraints_to_dict(o.constraints),
        "rigidity": o.rigidity,
        "compressibility_k": float(o.compressibility_k),
    }


def object_from_dict(d: dict) -> Object:
    return Object(
        id=d["id"],
        dimensions=tuple(_floats(d["dimensions"])),
        position=tuple(_floats(d["position"])),
        rotation=_rotation_or_identity(d.get("rotation")),
        mass_kg=float(d.get("mass_kg", 1.0)),
        constraints=constraints_from_dict(d.get("constraints")),
        rigidity=d.get("rigidity", "rigid"),
        compressibility_k=float(d.get("compressibility_k", 1.0)),
    )


def container_to_dict(c: Container) -> dict:
    return {
        "id": c.id,
        "dimensions": _floats(c.dimensions),
        "position": _floats(c.position),
        "rotation": _floats(c.rotation),
    }


def container_from_dict(d: dict) -> Container:
    return Container(
        id=d["id"],
        dimensions=tuple(_floats(d["dimensions"])),
        position=tuple(_floats(d.get("position", (0.0, 0.0, 0.0)))),
        rotation=_rotation_or_identity(d.get("rotation")),
    )


def scene_to_dict(scene: Scene) -> dict:
    return {
        "container": container_to_dict(scene.container),
        "objects": [object_to_dict(o) for o in scene.objects],
    }


def scene_from_dict(d: dict) -> Scene:
    return Scene(
        container=container_from_dict(d["container"]),
        objects=[object_from_dict(o) for o in d.get("objects", [])],
    )


# --- LiDAR scanner adapters --------------------------------------------------


def object_from_scanned_item(
    item: dict,
    *,
    position=(0.0, 0.0, 0.0),
    rotation=(0.0, 0.0, 0.0, 1.0),
    id: Optional[str] = None,
    **fields: Any,
) -> Object:
    """Adapt a `ScannedItem` JSON dict (centimetres, no pose) into an `Object`.

    cm -> m (/100); `dimensions = (width_m, height_m, depth_m)` so the
    scanner's width lands on local x, height on local y (vertical, unrotated),
    depth on local z -- matching `physics.schema`'s axis convention.

    The scanner never has a pose for the item, so `position`/`rotation`
    default to the origin/identity; pass real values once a solver or
    `apply_placements` has placed it. Extra `Object` fields (`mass_kg`,
    `constraints`, `rigidity`, `compressibility_k`, ...) go through **fields.
    """
    width_m = float(item["width"]) / 100.0
    depth_m = float(item["depth"]) / 100.0
    height_m = float(item["height"]) / 100.0
    kwargs: dict[str, Any] = dict(
        id=id if id is not None else item["id"],
        dimensions=(width_m, height_m, depth_m),
        position=tuple(float(c) for c in position),
        rotation=_rotation_or_identity(rotation),
    )
    kwargs.update(fields)
    return Object(**kwargs)


def object_from_box_fit(id: str, width_m: float, depth_m: float, height_m: float, center, axis, **fields: Any) -> Object:
    """Adapt a `BoxFit` (metres, yaw-only world pose) into a posed `Object`.

    `axis = (ax, 0, az)` is the world unit vector of the box's WIDTH edge.
    Rotating local +x about world Y by theta gives (cos theta, 0, -sin theta),
    so matching that to `axis` gives theta = atan2(-az, ax); the quaternion
    for a yaw of theta about Y is (0, sin(theta/2), 0, cos(theta/2)).
    Verified numerically in tests/test_io.py (obb_vertices of the resulting
    Object reproduces `axis` and `width_m` exactly).
    """
    ax, _ay, az = axis
    theta = math.atan2(-az, ax)
    rotation = (0.0, math.sin(theta / 2.0), 0.0, math.cos(theta / 2.0))
    kwargs: dict[str, Any] = dict(
        id=id,
        dimensions=(float(width_m), float(height_m), float(depth_m)),
        position=tuple(float(c) for c in center),
        rotation=rotation,
    )
    kwargs.update(fields)
    return Object(**kwargs)


# --- Solver placement adapters -----------------------------------------------


def _unknown_placement_ids(scene: Scene, placements: list[dict]) -> list[str]:
    known = {o.id for o in scene.objects}
    seen: list[str] = []
    for p in placements:
        pid = p.get("id")
        if pid not in known and pid not in seen:
            seen.append(pid)
    return seen


def apply_placements(scene: Scene, placements: list[dict]) -> Scene:
    """Return a NEW Scene with each placed object's pose replaced.

    Each placement dict is matched by `id`; accepts either `position`/
    `rotation` or `target_position`/`target_rotation` keys (both spellings
    are used across the team's docs). Objects not mentioned keep their
    current pose. Unknown ids raise `ValueError` listing them. Never mutates
    `scene` or `placements`.
    """
    unknown = _unknown_placement_ids(scene, placements)
    if unknown:
        raise ValueError(f"unknown object ids in placements: {unknown}")

    by_id = {p["id"]: p for p in placements}
    new_objects = []
    for o in scene.objects:
        p = by_id.get(o.id)
        if p is None:
            new_objects.append(o)
            continue
        pos = p.get("position", p.get("target_position"))
        rot = p.get("rotation", p.get("target_rotation"))
        changes = {}
        if pos is not None:
            changes["position"] = tuple(float(c) for c in pos)
        if rot is not None:
            changes["rotation"] = tuple(float(c) for c in rot)
        new_objects.append(replace(o, **changes) if changes else o)
    return Scene(container=scene.container, objects=new_objects)


def validate(scene: Scene, placements: Optional[list[dict]] = None) -> dict:
    """`validate(scene, placements)`: apply placements (if given), then
    `validate_layout`. Never raises -- unknown placement ids come back as a
    single MALFORMED_GEOMETRY violation (matching `validator.py`'s malformed
    shape) instead of propagating `apply_placements`'s ValueError.
    """
    if placements is not None:
        unknown = _unknown_placement_ids(scene, placements)
        if unknown:
            return {
                "valid": False,
                "score": 0.0,
                "violations": [
                    {
                        "type": "MALFORMED_GEOMETRY",
                        "object": unknown[0],
                        "detail": f"unknown object ids in placements: {unknown}",
                    }
                ],
                "warnings": [],
            }
        scene = apply_placements(scene, placements)
    return validate_layout(scene)


# --- Result JSON -------------------------------------------------------------


def _json_default(o: Any) -> Any:
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not JSON serializable: {o!r}")


def result_to_json(result: dict) -> str:
    """`validate_layout`/`validate` result -> JSON string. Handles the numpy
    scalars/arrays that leak into some result fields (e.g. `support_ratio`).
    """
    return json.dumps(result, default=_json_default)
