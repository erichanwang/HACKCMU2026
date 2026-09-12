"""Physics pre-pass: decide what the solver may pack, before the solver runs.

For each scanned item document (SCAN_OUTPUT.md: `dimensions` [width, height, depth] in
metres, `rigidity`, `compressibility`, `mass`, `keepUpright`) the physics layer builds
its own `Object` and hands packer3d a scenario item that already carries the verdicts:

* packable height: `compression_allowance_m` says how much height a soft item gives up
  (rigid and fragile: none; soft: height * (1 - 1/k), never more than 95%). packer3d
  gets that as the effective `compressibility` (= height / packable height) and packs the
  squashed box (`Item.compressed`).
* `fragile` (nothing may rest on it) and `keep_upright` are set only when physics asserts
  them, so packer3d's own "irregular scan defaults to fragile + upright" still applies.
* `mass` passes through as the solver's centre-of-mass input.

The document is otherwise untouched, so packer3d still classifies the shape from the
heightmap. `packer3d_adapter.validate_packer3d` grades the solver's output afterwards.
"""
from __future__ import annotations

from physics.compressibility import compression_allowance_m
from physics.schema import Constraints, Object


def physics_object(doc: dict) -> Object:
    """`Object` for one scanned item document, resting on the floor at the origin."""
    width, height, depth = (float(v) for v in doc["dimensions"])
    rigidity = doc.get("rigidity", "rigid")
    upright = bool(doc.get("keepUpright"))
    return Object(
        id=str(doc["id"]),
        dimensions=(width, height, depth),
        position=(0.0, height / 2.0, 0.0),
        mass_kg=float(doc.get("mass") or 0.0),
        constraints=Constraints(
            fragile=rigidity == "fragile",
            cannot_support_weight=rigidity == "fragile",
            keep_upright=upright,
            orientation_lock="this_side_up" if upright else None,
        ),
        rigidity="soft" if rigidity == "soft" else "rigid",
        compressibility_k=float(doc.get("compressibility") or 1.0),
    )


def packable(doc: dict) -> dict:
    """The packer3d scenario item for one scan: the document plus physics' verdicts."""
    obj = physics_object(doc)
    height = obj.dimensions[1]
    packable_height = height - compression_allowance_m(obj, height)
    out = doc | {"compressibility": height / packable_height, "mass": obj.mass_kg}
    if obj.constraints.fragile:
        out["fragile"] = True
    if obj.constraints.keep_upright:
        out["keep_upright"] = True
    return out


def prepare_items(docs) -> list[dict]:
    return [packable(d) for d in docs]
