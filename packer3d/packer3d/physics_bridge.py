"""Optional bridge to a physics/robotics-style scene schema: center position + quaternion
rotation, Y-up, X = right, Z = forward (this matches ARKit, and the ``physics.schema`` /
``physics.io`` modules used elsewhere in the HackCMU repo for validation and rendering).

packer3d's own convention is Z-up, position = the oriented bounding box's MIN corner,
orientation = an axis-permutation string. This module is the one-way (and, for placements,
round-trippable) conversion between the two, so packer3d never has to import anything
outside itself -- it emits plain dicts shaped exactly like the other schema's
``Object``/``Container``/placement dicts, and a caller with that package can do
``Object(**d)`` or ``physics.io.apply_placements(scene, placements)`` directly.

No dependency on the other package here: everything is derived from public geometry only.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from .models import BOX_ORIENTATIONS, Container, Item, PackResult, Placement

# world axis k of the *other* schema <- item's local axis perm[k], for a cylinder standing
# along world axis "z"/"x"/"y" (mirrors BOX_ORIENTATIONS but for the two lay-down axes of a
# cylinder whose own dims are always (2r, 2r, h) in local x/y/z).
_CYL_AXIS_PERM = {"z": (0, 1, 2), "x": (2, 0, 1), "y": (0, 2, 1)}

# packer3d world (x=length, y=width, z=up) -> physics world (X=right, Y=up, Z=forward).
# X <- packer.x, Y <- packer.z, Z <- -packer.y. Proper rotation (det=+1, orthogonal): verified
# in tests/test_physics_bridge.py both algebraically and by round-tripping a battery of
# placements through the exact quaternion<->matrix formula the physics package uses.
R_AXES = np.array([
    [1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0],
    [0.0, -1.0, 0.0],
])


def _signed_perm_matrix(perm) -> np.ndarray:
    """Rotation matrix for axis permutation ``perm`` (row k has a 1 in column perm[k]).

    A bare 0/1 permutation matrix is a proper rotation only for an even permutation; for an
    odd one (a single axis swap) it has determinant -1 (a reflection). Physically a box can
    always be reoriented to any face permutation using an actual rotation (no chirality
    issue for a shape with no handedness), so when the plain permutation is improper we flip
    one row's sign -- this yields one of the (always at least one) proper rotations that
    produce the same axis-aligned bounding box; which specific one doesn't matter for a
    symmetric-under-180-degree-turns box or cylinder.
    """
    R = np.zeros((3, 3))
    for k, j in enumerate(perm):
        R[k, j] = 1.0
    if np.linalg.det(R) < 0:
        R[2, :] *= -1.0
    return R


# Relabeling used on BOTH sides (not a coincidence -- see the derivation below): swap index 1
# and 2. World: packer axis1 (a horizontal "width" axis) <-> physics axis2 (depth); packer
# axis2 (up) <-> physics axis1 (up). Item-local: packer's own axis order (length,width,height)
# has width/height at indices 1/2; physics_object_dims re-orders to (width,height,depth), also
# swapping indices 1 and 2 relative to packer's own numbering.
_RELABEL = (0, 2, 1)


def _physics_local_perm(placement: Placement) -> tuple:
    """The rotation packer3d chose, re-expressed as a permutation directly in
    physics-local -> physics-world index terms (so identity in one frame is identity in both).

    Packer's orientation permutation ``perm`` is defined in packer's OWN local/world axis
    numbering: packer-world axis k <- packer-local axis perm[k]. Both the local item axes and
    the world axes get relabeled (independently) by ``_RELABEL`` going from packer's numbering
    to physics's numbering, so the equivalent permutation in physics-local/world terms is
    ``rho(m) = RELABEL[perm[RELABEL[m]]]`` -- derived by requiring the resulting world-aligned
    extents match packer3d's own placement.dims under both relabelings (verified in
    tests/test_physics_bridge.py against the placement geometry directly, independent of this
    formula).
    """
    perm = _CYL_AXIS_PERM[placement.axis] if placement.shape == "cylinder" else BOX_ORIENTATIONS[placement.orientation]
    g = _RELABEL
    return tuple(g[perm[g[m]]] for m in range(3))


def _item_local_rotation(placement: Placement) -> np.ndarray:
    """Physics-local -> physics-world rotation matrix for this placement's orientation.
    Identity orientation (no packer reorientation) maps to the identity matrix exactly."""
    return _signed_perm_matrix(_physics_local_perm(placement))


def matrix_to_quaternion(R) -> tuple:
    """3x3 proper rotation matrix -> unit quaternion ``(x, y, z, w)``.

    Standard branch-on-largest-diagonal (Shepperd's method) for numerical stability near any
    rotation angle. This is the exact inverse of the ``quat_to_matrix`` formula used by the
    physics package's ``physics/geometry.py`` (verified by round-trip in the test suite).
    """
    R = np.asarray(R, dtype=float)
    m00, m01, m02 = R[0]
    m10, m11, m12 = R[1]
    m20, m21, m22 = R[2]
    tr = m00 + m11 + m22
    if tr > 0:
        S = math.sqrt(tr + 1.0) * 2
        w = 0.25 * S
        x = (m21 - m12) / S
        y = (m02 - m20) / S
        z = (m10 - m01) / S
    elif m00 > m11 and m00 > m22:
        S = math.sqrt(1.0 + m00 - m11 - m22) * 2
        w = (m21 - m12) / S
        x = 0.25 * S
        y = (m01 + m10) / S
        z = (m02 + m20) / S
    elif m11 > m22:
        S = math.sqrt(1.0 + m11 - m00 - m22) * 2
        w = (m02 - m20) / S
        x = (m01 + m10) / S
        y = 0.25 * S
        z = (m12 + m21) / S
    else:
        S = math.sqrt(1.0 + m22 - m00 - m11) * 2
        w = (m10 - m01) / S
        x = (m02 + m20) / S
        y = (m12 + m21) / S
        z = 0.25 * S
    q = np.array([x, y, z, w])
    q = q / np.linalg.norm(q)
    return tuple(round(float(v), 12) for v in q)


def physics_object_dims(item: Item) -> list:
    """Item's own (unrotated) dims in the other schema's local axis order (x=width, y=height,
    z=depth) -- i.e. what goes in ``Object.dimensions``, *not* the world-aligned placement dims."""
    d = item.dims
    return [d[0], d[2], d[1]]


def physics_container_dict(container: Container) -> dict:
    """``Container`` dict in the other schema: center position, floor at Y=0, identity rotation.

    Raises ``ValueError`` for a cylindrical packer3d container: the other schema's
    ``Container`` has no shape field at all -- it is always an oriented box (see
    ``physics/schema.py``). Silently emitting a box-shaped container for a cylindrical one
    would make every corner of that box look like valid interior space to the other side's
    validator, when a real cylindrical container only fills roughly pi/4 of that box -- a
    validation pass that would be meaningless, not just approximate.
    """
    if container.shape != "box":
        raise ValueError(
            f"container {container.id!r} is shape={container.shape!r}; the physics schema has no "
            "cylindrical container (physics.schema.Container is always an oriented box), so bridging "
            "it would silently under-validate items placed near the corners of a real cylinder. "
            "Only box containers can be bridged with physics_container_dict()."
        )
    L, W, H = container.dims
    return {
        "id": container.id,
        "dimensions": [L, H, W],
        "position": [0.0, H / 2.0, 0.0],
        "rotation": [0.0, 0.0, 0.0, 1.0],
    }


def physics_object_dict(item: Item) -> dict:
    """``Object`` dict (no pose yet) -- dims + whatever else the caller wants to merge in
    (mass_kg, constraints, rigidity, ...) before building the physics schema's real ``Object``."""
    return {
        "id": item.id,
        "dimensions": physics_object_dims(item),
        "mass_kg": item.mass,
        "constraints": {"fragile": item.fragile, "keep_upright": item.keep_upright},
    }


def to_physics_placements(container: Container, result: PackResult) -> list:
    """Convert a ``PackResult`` into the ``[{id, position, rotation}, ...]`` list the other
    schema's ``physics.io.apply_placements`` / ``validate`` expect -- this is the solver's
    entire output contract per that package's integration doc. Coordinates: X=right, Y=up,
    Z=forward, origin at the container's horizontal center, floor at Y=0.

    Raises ``ValueError`` for a cylindrical container -- see ``physics_container_dict``'s
    docstring; the physics schema has no way to represent one, so its validator would check
    every object against the wrong (larger, box-shaped) envelope.
    """
    if container.shape != "box":
        raise ValueError(
            f"container {container.id!r} is shape={container.shape!r}; the physics schema can only "
            "represent a box container, so its validator would check placements against the wrong "
            "envelope for a real cylinder. Bridge a box container, or validate this one with "
            "packer3d.verify() only."
        )
    L, W, H = container.dims
    out = []
    for p in result.placements:
        R_final = _item_local_rotation(p)   # already expressed directly in physics-local/world terms
        quat = matrix_to_quaternion(R_final)
        cx, cy, cz = p.center
        pos = (cx - L / 2.0, cz, W / 2.0 - cy)
        out.append({
            "id": p.item_id,
            "position": [round(float(v), 9) for v in pos],
            "rotation": list(quat),
        })
    return out
