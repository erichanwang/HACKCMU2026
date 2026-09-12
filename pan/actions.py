"""Deterministic action-language generator (PAN.md section 8).

Turns a structured `PackingAction` into a grounded, natural-language physical
instruction for PAN -- derived only from the scene/action data, never from
invented attributes. A word like "black" may appear only if the caller passed
it in through `labels`.

Conventions (must match every helper below):
  - Axes/units are `physics.schema`'s: meters, X=right, Y=up, Z=forward.
  - "Left/right/front/rear" and the region grid are relative to the
    CONTAINER's own local frame (its position + quaternion), not world axes,
    so a rotated container still gets correct regions: +local_x = right,
    -local_x = left, +local_z = front (toward the traveler), -local_z = rear.
  - The container footprint is divided into thirds along local X (left /
    center / right) and local Z (rear / middle / front) to name a 3x3 grid of
    regions ("front-left corner", "middle-right", "center", ...). "Corner" is
    only appended when BOTH the row and column are extremes (not "middle" /
    "center").
  - Clockwise-from-above is defined as a NEGATIVE rotation about world +Y
    (right-hand rule): looking down from above (+Y toward the viewer), a
    positive right-hand-rule yaw about +Y is counterclockwise. Verified
    numerically in tests/test_pan_actions.py against `quat_to_matrix`
    directly, not just asserted.

Public API: `describe_action`, `describe_sequence`, `fill_action_text`.
Everything else here is a small pure helper, each independently unit-tested.
"""
from __future__ import annotations

import math
import re
from dataclasses import replace
from typing import Optional

import numpy as np

from physics.geometry import quat_to_matrix
from physics.schema import Container, Quat, Scene, Vec3
from physics.support import SupportResult, check_support
from pan.types import PackingAction, apply_action, object_by_id

# --------------------------------------------------------------------- words
# Kept as module-level constants so wording can be tuned without touching logic.
_FLOOR_PHRASE = "flat on the suitcase floor"
_UNSUPPORTED_PHRASE = "(unsupported)"
_STACK_TEMPLATE = "on top of the {names}"
_ROTATION_TEMPLATE = "rotated approximately {deg} degrees {direction} (seen from above)"
_CLOCKWISE = "clockwise"
_COUNTERCLOCKWISE = "counterclockwise"
_ORIENTATION_FLAT = "laid flat"
_ORIENTATION_END = "standing on end"
_ORIENTATION_SIDE = "on its side"
_KEEP_UPRIGHT_CLAUSE = "keeping it upright"
_FRAGILE_SUFFIX = " (fragile)"
_PICKUP_TEMPLATE = (
    "A traveler picks up the {label}, {location}, and places it {layer} "
    "in the {region}, {clauses}"
)
_CLOSING_OTHERS = "The other objects remain in their current positions."
_NEXT_TEMPLATE = "The {label} will be placed next."

_TRAILING_INDEX_RE = re.compile(r"_\d+$")
_EPS = 1e-9
_ROTATION_OMIT_THRESHOLD_DEG = 5.0
_ROTATION_ROUND_DEG = 5.0


# ---------------------------------------------------------- 1. naming (rule 1)
def humanize_label(object_id: str, labels: Optional[dict[str, str]] = None) -> str:
    """`labels.get(object_id)` if present, else a humanized id.

    Humanizing strips a trailing "_<digits>" (an instance suffix, e.g.
    "shoe_01" -> "shoe") and turns remaining underscores into spaces
    ("toiletry_bottle" -> "toiletry bottle").
    """
    if labels and object_id in labels:
        return labels[object_id]
    stripped = _TRAILING_INDEX_RE.sub("", object_id)
    return stripped.replace("_", " ")


# ------------------------------------------------------- container local frame
def _local_offset(container: Container, position: Vec3) -> tuple[float, float, float]:
    """World `position` expressed in the container's own local frame."""
    axes = quat_to_matrix(container.rotation)
    delta = np.asarray(position, dtype=float) - np.asarray(container.position, dtype=float)
    local = axes.T @ delta
    return float(local[0]), float(local[1]), float(local[2])


def _third(value: float, half: float, neg: str, mid: str, pos: str) -> str:
    bound = half / 3.0
    if value < -bound:
        return neg
    if value > bound:
        return pos
    return mid


def _combine_region(row: str, col: str) -> str:
    if row == "middle" and col == "center":
        return "center"
    if row == "middle":
        return f"middle-{col}"
    if col == "center":
        return f"{row}-center"
    return f"{row}-{col} corner"


def _region_from_local(lx: float, lz: float, half_x: float, half_z: float) -> str:
    col = _third(lx, half_x, "left", "center", "right")
    row = _third(lz, half_z, "rear", "middle", "front")
    return _combine_region(row, col)


# --------------------------------------------------- 2. current location (rule 2)
def current_location_phrase(container: Container, position: Vec3) -> str:
    """"currently in the <region> of the suitcase" if `position` is inside
    the container's footprint and at/above its floor, else "currently outside
    the suitcase to the <left/right/front/rear> of it" (the largest-magnitude
    local X/Z offset from the container center)."""
    lx, ly, lz = _local_offset(container, position)
    half_x, half_y, half_z = (d / 2.0 for d in container.dimensions)
    inside = abs(lx) <= half_x + _EPS and abs(lz) <= half_z + _EPS and ly >= -half_y - _EPS
    if inside:
        region = _region_from_local(lx, lz, half_x, half_z)
        return f"currently in the {region} of the suitcase"
    if abs(lx) >= abs(lz):
        direction = "right" if lx > 0 else "left"
    else:
        direction = "front" if lz > 0 else "rear"
    return f"currently outside the suitcase to the {direction} of it"


# ------------------------------------------------------- 3. target region + layer
def target_region(container: Container, position: Vec3) -> str:
    """Name of the container-footprint-thirds region containing `position`,
    projected into the container's local XZ frame."""
    lx, _ly, lz = _local_offset(container, position)
    half_x = container.dimensions[0] / 2.0
    half_z = container.dimensions[2] / 2.0
    return _region_from_local(lx, lz, half_x, half_z)


def layer_phrase(support: SupportResult, labels: Optional[dict[str, str]] = None) -> str:
    """What the object rests on, from a `check_support` result: resting on
    other object(s) beats the floor; `support_ratio == 0` is unsupported --
    the physics validator will reject that layout, but the text must not lie
    about it being flat on the floor."""
    non_floor = [s for s in support.supporting_objects if s != "container_floor"]
    if non_floor:
        names = " and ".join(humanize_label(s, labels) for s in non_floor)
        return _STACK_TEMPLATE.format(names=names)
    if support.support_ratio <= 0.0:
        return _UNSUPPORTED_PHRASE
    if "container_floor" in support.supporting_objects:
        return _FLOOR_PHRASE
    return _UNSUPPORTED_PHRASE


# ------------------------------------------------------------ 4. rotation (rule 4)
def _local_x_yaw_deg(rotation: Quat) -> float:
    """Yaw (degrees) of the local +x axis in the world XZ plane, defined so
    that a positive right-hand-rule rotation about +Y increases this value
    (see module docstring's clockwise-from-above convention)."""
    axis = quat_to_matrix(rotation)[:, 0]
    return -math.degrees(math.atan2(axis[2], axis[0]))


def _wrap180(deg: float) -> float:
    """Wrap an angle in degrees to (-180, 180]."""
    wrapped = deg % 360.0
    if wrapped > 180.0:
        wrapped -= 360.0
    return wrapped


def rotation_phrase(before_rotation: Quat, after_rotation: Quat) -> Optional[str]:
    """None if the yaw change is under 5 degrees, else "rotated approximately
    N degrees clockwise/counterclockwise (seen from above)" with N rounded to
    the nearest 5. Negative yaw change (right-hand rule about +Y) = clockwise
    seen from above; positive = counterclockwise."""
    delta = _wrap180(_local_x_yaw_deg(after_rotation) - _local_x_yaw_deg(before_rotation))
    if abs(delta) < _ROTATION_OMIT_THRESHOLD_DEG:
        return None
    rounded = round(delta / _ROTATION_ROUND_DEG) * _ROTATION_ROUND_DEG
    direction = _CLOCKWISE if rounded < 0 else _COUNTERCLOCKWISE
    return _ROTATION_TEMPLATE.format(deg=int(abs(rounded)), direction=direction)


# --------------------------------------------------------- 5. orientation (rule 5)
def orientation_phrase(dimensions: Vec3, rotation: Quat) -> str:
    """"laid flat" if the object's smallest dimension ends up vertical under
    `rotation`, "standing on end" if its largest does, else "on its side"."""
    axes = quat_to_matrix(rotation)
    vertical_component = np.abs(axes[1, :])  # world-Y component of each local axis
    idx = int(np.argmax(vertical_component))
    d = dimensions[idx]
    if math.isclose(d, min(dimensions), rel_tol=1e-9, abs_tol=1e-9):
        return _ORIENTATION_FLAT
    if math.isclose(d, max(dimensions), rel_tol=1e-9, abs_tol=1e-9):
        return _ORIENTATION_END
    return _ORIENTATION_SIDE


# ------------------------------------------------------------- 6. closing (rule 6)
def closing_phrase(
    next_action: Optional[PackingAction] = None, labels: Optional[dict[str, str]] = None
) -> str:
    """The fixed "other objects" sentence, plus an optional "will be placed
    next" sentence naming `next_action`'s object."""
    parts = [_CLOSING_OTHERS]
    if next_action is not None:
        nxt = (labels or {}).get(next_action.object_id) or next_action.label or humanize_label(next_action.object_id)
        parts.append(_NEXT_TEMPLATE.format(label=nxt))
    return " ".join(parts)


# ------------------------------------------------------------------- public API
def describe_action(
    scene_before: Scene,
    action: PackingAction,
    *,
    labels: Optional[dict[str, str]] = None,
    next_action: Optional[PackingAction] = None,
) -> str:
    """A grounded, deterministic, 2-4 sentence physical instruction for
    `action`, derived only from `scene_before`/`action`/`labels`."""
    obj_before = object_by_id(scene_before, action.object_id)
    # Precedence: explicit labels dict > the action's own semantic label > humanized id.
    label = (labels or {}).get(action.object_id) or action.label or humanize_label(action.object_id)
    location = current_location_phrase(scene_before.container, obj_before.position)
    region = target_region(scene_before.container, action.target_position)

    scene_after = apply_action(scene_before, action)
    support = next(
        s for s in check_support(scene_after) if s.object_id == action.object_id
    )
    layer = layer_phrase(support, labels)

    rotation = rotation_phrase(obj_before.rotation, action.target_rotation)
    orientation = orientation_phrase(obj_before.dimensions, action.target_rotation)

    clauses = []
    if rotation:
        clauses.append(rotation)
    clauses.append(orientation)
    if obj_before.constraints.keep_upright:
        clauses.append(_KEEP_UPRIGHT_CLAUSE)

    sentence = _PICKUP_TEMPLATE.format(
        label=label, location=location, layer=layer, region=region, clauses=", ".join(clauses)
    )
    if obj_before.constraints.fragile:
        sentence += _FRAGILE_SUFFIX
    sentence += "."

    return sentence + " " + closing_phrase(next_action, labels)


def describe_sequence(
    scene: Scene, actions: list[PackingAction], labels: Optional[dict[str, str]] = None
) -> list[str]:
    """`describe_action` for each step in order, applying each action to the
    scene first so step k's "currently..." clause reflects steps < k."""
    texts = []
    current = scene
    for i, action in enumerate(actions):
        next_action = actions[i + 1] if i + 1 < len(actions) else None
        texts.append(describe_action(current, action, labels=labels, next_action=next_action))
        current = apply_action(current, action)
    return texts


def fill_action_text(
    scene: Scene, action: PackingAction, labels: Optional[dict[str, str]] = None
) -> PackingAction:
    """Return a copy of `action` with `.text` set to `describe_action(...)`."""
    text = describe_action(scene, action, labels=labels)
    return replace(action, text=text)
