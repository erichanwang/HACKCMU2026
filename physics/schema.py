"""Scene schema shared by every physics submodule.

Units: meters everywhere.
Coordinates: X = right, Y = up, Z = forward (right-handed).
Rotation: quaternion (x, y, z, w), unit-length, world orientation of the object's
local axes (dimensions are measured along local axes before rotation).
Dimensions: (length, width, height) full extents along local x, y, z. For a
`rigidity="soft"` or `"semi"` object these are its LOOSE (uncompressed)
dimensions — the OBB the geometry layer measured before packing, matching
`compressibility_k` (see below).
IDs: stable strings, unique within a Scene.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]


@dataclass
class Constraints:
    fragile: bool = False
    keep_upright: bool = False
    cannot_support_weight: bool = False
    heavy: bool = False
    # None | "this_side_up" | "flat_only" | "horizontal"
    orientation_lock: Optional[str] = None


@dataclass
class Object:
    id: str
    dimensions: Vec3
    position: Vec3
    rotation: Quat = (0.0, 0.0, 0.0, 1.0)
    mass_kg: float = 1.0
    constraints: Constraints = field(default_factory=Constraints)
    # "rigid" | "semi" | "soft". Rigid = no compression tolerance, ever.
    rigidity: str = "rigid"
    # >= 1.0. A soft/semi item's loose volume V can occupy a void as small as
    # V/k (OVERVIEW.md's compressibility model). Ignored when rigidity="rigid".
    # Default 1.0 (no compression) so existing rigid-object scenes are unaffected.
    compressibility_k: float = 1.0


@dataclass
class Container:
    id: str
    dimensions: Vec3
    position: Vec3 = (0.0, 0.0, 0.0)
    rotation: Quat = (0.0, 0.0, 0.0, 1.0)


@dataclass
class Scene:
    container: Container
    objects: list[Object]
