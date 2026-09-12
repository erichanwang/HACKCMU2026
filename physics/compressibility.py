"""Compressibility-derived compression allowance for soft/semi-rigid objects.

This is a heuristic, not a deformation simulator. OVERVIEW.md's compressibility
model says an item of loose volume V fits any connected void of at least V/k
(`Object.compressibility_k`). We crudely reuse that same fraction, `1 - 1/k`,
as the fraction of an object's *linear* extent along one axis that could
plausibly compress away -- scaled down by how willing the object's rigidity
class is to actually use that headroom (`RIGIDITY_ALLOWANCE_FRACTION`), and
hard-capped at 95% of the extent so nothing is ever treated as compressing
itself away to nothing.

This single scalar-per-axis model is intentionally crude: real fabric/foam
compression is anisotropic (squishes differently in different directions)
and load-dependent (how hard it's being pushed), not a single k-derived
number. Good enough for a hackathon MVP; a real deformation/FEM model is out
of scope here.
"""
from __future__ import annotations

import numpy as np

from physics.geometry import OBB
from physics.schema import Object

# How much of an object's theoretical max compression (derived from k) it is
# allowed to use before a physics check treats overlap/penetration as a real
# violation, by rigidity class. "rigid" is always 0.0 -- a rigid object
# ignores `compressibility_k` entirely, because k (a compression factor) is
# meaningless for something that never compresses.
RIGIDITY_ALLOWANCE_FRACTION = {"rigid": 0.0, "semi": 0.5, "soft": 1.0}

# Never treat more than this fraction of an object's own extent as
# "compressible away" -- compressing 100% of an object's size to zero is
# nonsensical, so cap well short of it.
_MAX_COMPRESSION_FRACTION = 0.95


def compression_allowance_m(obj: Object, extent_m: float) -> float:
    """How much overlap/penetration (meters), along one axis, is plausible
    squish for `obj` rather than a real physical violation.

    `extent_m` is the object's own full extent along whatever axis the
    caller is checking (a collision MTV axis, a container wall axis, ...);
    this function is axis-agnostic -- the caller picks the extent.

    Rigid objects always get 0.0, regardless of `compressibility_k`. For
    semi/soft objects: `extent_m * (1 - 1/max(1, k)) * fraction`, clamped to
    `[0, 0.95 * extent_m]`.
    """
    if obj.rigidity == "rigid":
        return 0.0
    fraction = RIGIDITY_ALLOWANCE_FRACTION.get(obj.rigidity, 0.0)
    k = max(1.0, float(obj.compressibility_k))
    allowance = extent_m * (1.0 - 1.0 / k) * fraction
    return float(min(max(0.0, allowance), _MAX_COMPRESSION_FRACTION * extent_m))


def axis_projected_extent_m(obb: OBB, axis: np.ndarray) -> float:
    """Full extent (meters) of `obb` projected onto world-space unit `axis`.

    Same `sum(|axis . column_k| * half_extent_k)` projection `check_collision`
    already uses internally to project a box's half-extents onto an
    arbitrary axis, doubled here to a full extent. Reimplemented (not
    imported) per physics/collision.py being a "don't touch" module for this
    task -- it's 3 lines of scalar math, not the quaternion/rotation logic.
    """
    half = float(np.sum(np.abs(np.asarray(axis) @ obb.axes) * obb.half_extents))
    return 2.0 * half


def combined_collision_allowance_m(
    a: Object, b: Object, mtv_axis: np.ndarray, obb_a: OBB, obb_b: OBB
) -> float:
    """Total compression allowance (meters) for a colliding pair along
    `mtv_axis`: each object's own allowance, from its own extent projected
    onto that axis, summed.
    """
    extent_a = axis_projected_extent_m(obb_a, mtv_axis)
    extent_b = axis_projected_extent_m(obb_b, mtv_axis)
    return compression_allowance_m(a, extent_a) + compression_allowance_m(b, extent_b)


def container_wall_allowance_m(obj: Object, wall_axis_extent_m: float) -> float:
    """Compression allowance (meters) for `obj` bulging past one container
    wall. `wall_axis_extent_m` is `obj`'s own full extent along the violated
    container axis (the caller projects the object's OBB onto that axis with
    `axis_projected_extent_m`, since a rotated object's extent along a given
    container axis isn't simply one of its own `dimensions` entries). Thin
    wrapper around `compression_allowance_m` -- kept as its own function so
    validator.py's containment call site reads clearly.
    """
    return compression_allowance_m(obj, wall_axis_extent_m)
