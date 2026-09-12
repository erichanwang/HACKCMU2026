"""Physics pre-pass: decide what the solver may pack, before the solver runs.

For each scanned item document (SCAN_OUTPUT.md: `dimensions` [width, height, depth] in
metres, `rigidity`, `compressibility`, `mass`, `keepUpright`) the physics layer builds
its own `Object` and hands packer3d a scenario item that already carries the verdicts:

* packable height: `compression_allowance_m` says how much height a soft item gives up
  (rigid and fragile: none; soft: height * (1 - 1/k), never more than 95%). packer3d
  gets that as the effective `compressibility` (= height / packable height) and packs the
  squashed box (`Item.compressed`).
* `fold_options`: for a soft item, the handful of other bounding boxes a person could
  refold the same clothes into at about the same volume (flat / folded in half / rolled),
  most preferred first. Nothing consumes this yet -- a later change teaches the solver to
  pick among them; today it is an extra key the scenario loader ignores.
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
    footprint = doc.get("footprint")
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
        # LiDAR hull, straight from the scan doc -- same (x, z) convention as
        # `physics.io.object_from_scanned_item`. Convexity/bounds are validated
        # lazily wherever the geometry is actually built (`geometry.footprint_local`).
        footprint=[(float(x), float(z)) for x, z in footprint] if footprint else None,
    )


# Fold geometry, from real folded clothes rather than from maths:
#
#   LOOKED UP  a t-shirt folded the ordinary retail way is about 30 x 22 cm.
#   LOOKED UP  the same t-shirt rolled is about 28 cm long and 9 cm across.
#
# Everything below is ESTIMATED, chosen so those two shapes come out of the same
# stack of fabric:
#   * folding in half halves the longer face side and doubles the thickness --
#     volume is conserved exactly, and 30 x 22 x 2.5 cm folds to 22 x 15 x 5 cm.
#   * a roll keeps most of its length (`_ROLL_LENGTH_FRACTION`: 30 cm of shirt
#     rolls up about 28 cm long) and trades the rest for a square-ish cross
#     section, which is looser than a fold because rolling traps air
#     (`_ROLL_BULK`). Those two put the 30 x 22 x 2.5 cm stack at 28 x 8.8 x 8.8
#     cm, against the 28 x 9 cm that was looked up.
#   * `_MIN_FOLD_DIM_M`: below about 6 cm a fold stops being a fold and becomes a
#     wad, so no option is allowed to go thinner (an item already thinner than
#     that keeps its own thinnest dimension as the floor instead).
_ROLL_LENGTH_FRACTION = 0.93
_ROLL_BULK = 1.3
_MIN_FOLD_DIM_M = 0.06


def fold_options(dimensions) -> list[list[float]]:
    """Alternative bounding boxes, at about constant volume, that a person could
    actually fold this soft item into -- `[width, height, depth]` in metres, most
    preferred first, starting with the box as scanned.

    The thinnest axis is read as the stack's thickness and the other two as its
    face (see `layer_axis_index`). Every option is written back thinnest-to-longest
    onto the item's own thin/mid/long axes, which is just a canonical spelling of
    the box -- the solver rotates candidate boxes itself.
    """
    dims = [float(v) for v in dimensions]
    order = sorted(range(3), key=lambda i: dims[i])  # thin, mid, long
    thin, mid, long_ = (dims[i] for i in order)
    volume = thin * mid * long_

    rolled_long = _ROLL_LENGTH_FRACTION * long_
    rolled_side = (_ROLL_BULK * volume / rolled_long) ** 0.5
    shapes = [
        (thin, mid, long_),                      # flat, as scanned
        (2.0 * thin, mid, long_ / 2.0),          # folded in half across the long side
        (rolled_side, rolled_side, rolled_long),  # rolled up
    ]

    floor = min(_MIN_FOLD_DIM_M, thin)
    out = []
    for shape in shapes:
        if min(shape) < floor:
            continue
        box = [0.0, 0.0, 0.0]
        for axis, value in zip(order, sorted(shape)):
            box[axis] = value
        if box not in out:
            out.append(box)
    return out


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
    if doc.get("rigidity") == "soft":  # only soft things refold; rigid and fragile keep their shape
        out["fold_options"] = fold_options(obj.dimensions)
    return out


def prepare_items(docs) -> list[dict]:
    return [packable(d) for d in docs]
