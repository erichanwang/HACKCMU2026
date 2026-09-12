"""Data model: items, containers, obstacles, placements and results.

Coordinates are right-handed: x = length, y = width, z = up, origin at the container's
minimum corner. A cylindrical container is vertical with ``dims = (2R, 2R, H)`` and its
axis at ``(R, R)``.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import NamedTuple, Optional, Sequence

from .geometry import EPS, is_finite_number, rnd3


class Orientation(NamedTuple):
    """One legal 90-degree orientation of an item.

    ``name`` is the axis permutation for boxes (``"xzy"`` = world x <- item x,
    world y <- item z, world z <- item y) or ``"cyl_axis_{x|y|z}"`` for cylinders.
    ``dims`` is the oriented bounding box; ``axis`` is the world axis of a cylinder.
    """
    name: str
    dims: tuple
    axis: Optional[str]


# world axis k takes the item's original axis perm[k]
BOX_ORIENTATIONS = {
    "xyz": (0, 1, 2), "xzy": (0, 2, 1), "yxz": (1, 0, 2),
    "yzx": (1, 2, 0), "zxy": (2, 0, 1), "zyx": (2, 1, 0),
}
CYL_ORIENTATIONS = {"z": "cyl_axis_z", "x": "cyl_axis_x", "y": "cyl_axis_y"}


def _check_positive(value, what: str, allow_zero: bool = False):
    if not is_finite_number(value):
        raise ValueError(f"{what} must be a finite number, got {value!r}")
    if value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{what} must be {'>= 0' if allow_zero else '> 0'}, got {value!r}")


def _check_dims(dims, what: str) -> tuple:
    if dims is None or len(dims) != 3:
        raise ValueError(f"{what} must have exactly 3 entries, got {dims!r}")
    out = []
    for k, v in enumerate(dims):
        _check_positive(v, f"{what}[{k}]")
        out.append(float(v))
    return tuple(out)


@dataclass(frozen=True)
class Item:
    """A rectangular box or a cylinder to be packed.

    Use ``Item.box(...)`` / ``Item.cylinder(...)`` rather than the constructor.
    ``dims`` is always the axis-aligned bounding box in the item's own frame
    (for cylinders ``(2r, 2r, h)`` with the axis along item-z).
    """
    id: str
    shape: str
    dims: tuple
    mass: float = 0.0
    fragile: bool = False
    keep_upright: bool = False
    allow_lay_down: bool = True
    priority: float = 1.0
    radius: Optional[float] = None
    height: Optional[float] = None
    scan_shape: Optional[str] = None     # original scanned shape ("box","cylinder","irregular",...) if any
    true_volume: Optional[float] = None  # measured volume from a mesh, overrides the analytic volume
    scan_yaw_deg: float = 0.0            # rotation about z applied to the scan to get the tight bbox

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("item id must be a non-empty string")
        if self.true_volume is not None:
            _check_positive(self.true_volume, f"item {self.id} true_volume")
        if self.shape not in ("box", "cylinder"):
            raise ValueError(f"item {self.id}: shape must be 'box' or 'cylinder', got {self.shape!r}")
        object.__setattr__(self, "dims", _check_dims(self.dims, f"item {self.id} dims"))
        _check_positive(self.mass, f"item {self.id} mass", allow_zero=True)
        _check_positive(self.priority, f"item {self.id} priority")
        if self.shape == "cylinder":
            _check_positive(self.radius, f"item {self.id} radius")
            _check_positive(self.height, f"item {self.id} height")
        object.__setattr__(self, "mass", float(self.mass))
        object.__setattr__(self, "priority", float(self.priority))

    # ---- constructors -------------------------------------------------
    @staticmethod
    def box(id: str, length: float, width: float, height: float, mass: float = 0.0, *,
            fragile: bool = False, keep_upright: bool = False, priority: float = 1.0) -> "Item":
        return Item(id, "box", (length, width, height), mass, fragile, keep_upright, True, priority)

    @staticmethod
    def cylinder(id: str, radius: float, height: float, mass: float = 0.0, *,
                 fragile: bool = False, keep_upright: bool = False, allow_lay_down: bool = True,
                 priority: float = 1.0) -> "Item":
        _check_positive(radius, f"item {id} radius")
        _check_positive(height, f"item {id} height")
        return Item(id, "cylinder", (2 * radius, 2 * radius, height), mass, fragile, keep_upright,
                    allow_lay_down, priority, radius=float(radius), height=float(height))

    @staticmethod
    def from_scan(id: str, shape: str, length: float, depth: float, height: float, mass: float = 0.0, *,
                  fragile: Optional[bool] = None, keep_upright: bool = False, allow_lay_down: bool = True,
                  priority: float = 1.0) -> "Item":
        """Build an item from a lidar measurement: bounding dims (length, depth, height) + shape.

        ``shape`` is "box" or "cylinder" (case-insensitive; "cuboid"/"cube" and "cyl"/"can"/"bottle"
        also accepted).  Any other shape (oval, trapezoid, mannequin, ...) is packed as its bounding
        box with ``fragile=True`` by default (nothing may rest on a non-flat top).
        A cylinder is assumed to stand along its height, radius = min(length, depth)/2.
        Mass is unknown to the scanner; leave it 0 and the CoM falls back to the volume centroid,
        or pass a measured/estimated mass.
        """
        kind = str(shape).strip().lower()
        if kind in ("box", "cuboid", "cube", "rect", "rectangular"):
            return Item.box(id, length, depth, height, mass, fragile=bool(fragile), keep_upright=keep_upright,
                            priority=priority)
        if kind in ("cylinder", "cyl", "can", "bottle", "tube", "round"):
            _check_positive(length, f"item {id} length")
            _check_positive(depth, f"item {id} depth")
            return Item.cylinder(id, min(length, depth) / 2.0, height, mass, fragile=bool(fragile),
                                 keep_upright=keep_upright, allow_lay_down=allow_lay_down, priority=priority)
        # anything else (oval, trapezoid, mannequin, ...) packs as its bounding box; its top is
        # not flat, so by default nothing is allowed to rest on it (fragile=True).
        it = Item.box(id, length, depth, height, mass, fragile=True if fragile is None else fragile,
                      keep_upright=keep_upright, priority=priority)
        object.__setattr__(it, "scan_shape", kind or "irregular")
        return it

    @staticmethod
    def from_mesh(id: str, vertices, faces=None, mass: float = 0.0, *, fragile: Optional[bool] = None,
                  keep_upright: bool = False, allow_lay_down: bool = True, priority: float = 1.0,
                  yaw_search_deg: float = 1.0, classify: bool = True) -> "Item":
        """Build an item from a scanned mesh (``vertices`` (N,3), optional triangle ``faces`` (M,3)).

        * The mesh is rotated about z (0..90 deg in ``yaw_search_deg`` steps) to find the smallest
          footprint; the chosen yaw is stored in ``scan_yaw_deg`` for the frontend.
        * With faces, the closed-mesh volume is measured and the shape is classified:
          volume/bbox ~ 1 -> box, ~ pi/4 with a square footprint -> cylinder, otherwise irregular.
        * Irregular objects pack as their bounding box and default to ``fragile=True``
          (their top is not flat, so nothing is stacked on them).
        """
        import numpy as np
        V = np.asarray(vertices, dtype=float).reshape(-1, 3)
        if len(V) < 4 or not np.all(np.isfinite(V)):
            raise ValueError(f"item {id}: mesh needs >= 4 finite vertices")
        # tight footprint: search yaw about z
        best_yaw, best_area, best_dims = 0.0, math.inf, None
        step = max(0.1, float(yaw_search_deg))
        xy = V[:, :2]
        for deg in np.arange(0.0, 90.0, step):
            t = math.radians(deg)
            c, s_ = math.cos(t), math.sin(t)
            rx = xy[:, 0] * c - xy[:, 1] * s_
            ry = xy[:, 0] * s_ + xy[:, 1] * c
            ext = (rx.max() - rx.min(), ry.max() - ry.min())
            area = ext[0] * ext[1]
            if area < best_area - 1e-12:
                best_yaw, best_area, best_dims = float(deg), area, ext
        dims = (best_dims[0], best_dims[1], float(V[:, 2].max() - V[:, 2].min()))
        if any(d <= 0 for d in dims):
            raise ValueError(f"item {id}: mesh is flat along one axis (dims {dims})")
        bbox_vol = dims[0] * dims[1] * dims[2]
        # volume from closed triangle mesh (signed tetrahedra)
        vol = None
        if faces is not None:
            F = np.asarray(faces, dtype=int).reshape(-1, 3)
            if len(F):
                a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
                vol = abs(float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0)
                if not (vol > 0):
                    vol = None
        kind = "irregular"
        if classify and vol is not None:
            ratio = vol / bbox_vol
            square = abs(dims[0] - dims[1]) <= 0.08 * max(dims[0], dims[1])
            if ratio >= 0.92:
                kind = "box"
            elif square and abs(ratio - math.pi / 4) <= 0.08:
                kind = "cylinder"
        elif classify and vol is None:
            kind = "box"   # no faces -> can only trust the bounding box
        if kind == "box":
            it = Item.box(id, dims[0], dims[1], dims[2], mass, fragile=bool(fragile), keep_upright=keep_upright,
                          priority=priority)
        elif kind == "cylinder":
            it = Item.cylinder(id, min(dims[0], dims[1]) / 2.0, dims[2], mass, fragile=bool(fragile),
                               keep_upright=keep_upright, allow_lay_down=allow_lay_down, priority=priority)
        else:
            it = Item.box(id, dims[0], dims[1], dims[2], mass, fragile=True if fragile is None else fragile,
                          keep_upright=keep_upright, priority=priority)
        object.__setattr__(it, "scan_shape", kind)
        object.__setattr__(it, "scan_yaw_deg", best_yaw)
        if vol is not None:
            object.__setattr__(it, "true_volume", float(vol))
        return it

    # ---- derived ------------------------------------------------------
    @property
    def bbox_volume(self) -> float:
        return self.dims[0] * self.dims[1] * self.dims[2]

    @property
    def volume(self) -> float:
        """True volume (mesh volume if scanned, pi r^2 h for cylinders, else the box)."""
        if self.true_volume is not None:
            return min(self.true_volume, self.bbox_volume)
        if self.shape == "cylinder":
            return math.pi * self.radius ** 2 * self.height
        return self.bbox_volume

    def orientations(self) -> list:
        """Legal orientations, de-duplicated by oriented bounding box."""
        out, seen = [], set()
        if self.shape == "box":
            for name, perm in BOX_ORIENTATIONS.items():
                if self.keep_upright and perm[2] != 2:
                    continue
                d = (self.dims[perm[0]], self.dims[perm[1]], self.dims[perm[2]])
                key = rnd3(d)
                if key in seen:
                    continue
                seen.add(key)
                out.append(Orientation(name, d, None))
        else:
            r2, h = 2 * self.radius, self.height
            axes = [("z", (r2, r2, h))]
            if not self.keep_upright and self.allow_lay_down:
                axes += [("x", (h, r2, r2)), ("y", (r2, h, r2))]
            for ax, d in axes:
                key = rnd3(d)
                if key in seen:
                    continue
                seen.add(key)
                out.append(Orientation(CYL_ORIENTATIONS[ax], d, ax))
        return out

    def fits_in(self, container: "Container") -> bool:
        """Static check: some legal orientation fits inside the empty container."""
        return any(container.fits_dims(o.dims) for o in self.orientations())


@dataclass(frozen=True)
class Obstacle:
    """A fixed axis-aligned block inside the container (hatch, wheel arch, ...)."""
    id: str
    position: tuple
    dims: tuple

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("obstacle id must be a non-empty string")
        if self.position is None or len(self.position) != 3:
            raise ValueError(f"obstacle {self.id}: position must have 3 entries")
        for k, v in enumerate(self.position):
            if not is_finite_number(v) or v < -EPS:
                raise ValueError(f"obstacle {self.id}: position[{k}] must be a finite number >= 0")
        object.__setattr__(self, "position", tuple(float(v) for v in self.position))
        object.__setattr__(self, "dims", _check_dims(self.dims, f"obstacle {self.id} dims"))

    @property
    def volume(self) -> float:
        return self.dims[0] * self.dims[1] * self.dims[2]


@dataclass
class Container:
    """The bin: a box or a vertical cylinder.

    ``min_support`` is the fraction of an item's base that must rest on flush solids
    (floor, obstacles, other items) under gravity. ``com_target`` defaults to the
    container centre in x/y and to z=0 (gravity) or z=H/2 (microgravity);
    ``com_axis_weights`` default to (1, 1, 0.5) / (1, 1, 0.1) so lateral offset dominates.
    """
    id: str
    dims: tuple
    shape: str = "box"
    gravity: bool = True
    max_mass: float = math.inf
    obstacles: Sequence[Obstacle] = field(default_factory=tuple)
    min_support: float = 0.7
    com_target: Optional[tuple] = None
    com_axis_weights: Optional[tuple] = None

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("container id must be a non-empty string")
        if self.shape not in ("box", "cylinder"):
            raise ValueError(f"container shape must be 'box' or 'cylinder', got {self.shape!r}")
        self.dims = _check_dims(self.dims, "container dims")
        if self.shape == "cylinder" and abs(self.dims[0] - self.dims[1]) > EPS:
            raise ValueError("cylindrical container needs dims = (2R, 2R, H) with equal x/y")
        if isinstance(self.max_mass, bool) or not isinstance(self.max_mass, (int, float)) or math.isnan(self.max_mass) or self.max_mass < 0:
            raise ValueError(f"max_mass must be a number >= 0 (or inf), got {self.max_mass!r}")
        self.max_mass = float(self.max_mass)
        if not is_finite_number(self.min_support) or not (0.0 <= self.min_support <= 1.0):
            raise ValueError("min_support must be in [0, 1]")
        self.obstacles = tuple(self.obstacles)
        ids = set()
        for ob in self.obstacles:
            if not isinstance(ob, Obstacle):
                raise ValueError("obstacles must be Obstacle instances")
            if ob.id in ids:
                raise ValueError(f"duplicate obstacle id {ob.id!r}")
            ids.add(ob.id)
            for k in range(3):
                if ob.position[k] + ob.dims[k] > self.dims[k] + EPS:
                    raise ValueError(f"obstacle {ob.id} sticks out of the container along axis {k}")
        if self.com_target is not None:
            self.com_target = tuple(float(v) for v in _check_target(self.com_target))
        if self.com_axis_weights is not None:
            w = tuple(float(v) for v in self.com_axis_weights)
            if len(w) != 3 or any(not is_finite_number(v) or v < 0 for v in w):
                raise ValueError("com_axis_weights must be 3 non-negative numbers")
            self.com_axis_weights = w

    # ---- geometry -----------------------------------------------------
    @property
    def radius(self) -> float:
        return self.dims[0] / 2.0

    @property
    def volume(self) -> float:
        if self.shape == "cylinder":
            return math.pi * self.radius ** 2 * self.dims[2]
        return self.dims[0] * self.dims[1] * self.dims[2]

    @property
    def usable_volume(self) -> float:
        return max(0.0, self.volume - sum(ob.volume for ob in self.obstacles))

    def fits_dims(self, d) -> bool:
        if any(d[k] > self.dims[k] + EPS for k in range(3)):
            return False
        if self.shape == "cylinder":
            return math.hypot(d[0], d[1]) <= self.dims[0] + EPS
        return True

    def effective_com_target(self) -> tuple:
        if self.com_target is not None:
            return self.com_target
        z = 0.0 if self.gravity else self.dims[2] / 2.0
        return (self.dims[0] / 2.0, self.dims[1] / 2.0, z)

    def effective_com_axis_weights(self) -> tuple:
        if self.com_axis_weights is not None:
            return self.com_axis_weights
        return (1.0, 1.0, 0.5 if self.gravity else 0.1)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "shape": self.shape, "dims": list(self.dims), "gravity": self.gravity,
            "max_mass": self.max_mass if math.isfinite(self.max_mass) else None,
            "min_support": self.min_support,
            "obstacles": [{"id": o.id, "position": list(o.position), "dims": list(o.dims)} for o in self.obstacles],
            "com_target": list(self.effective_com_target()),
            "com_axis_weights": list(self.effective_com_axis_weights()),
        }


def _check_target(t):
    if t is None or len(t) != 3 or any(not is_finite_number(v) for v in t):
        raise ValueError("com_target must be 3 finite numbers")
    return t


@dataclass
class Placement:
    """Where one item ended up. ``position`` is the MIN corner of the oriented bounding box."""
    item_id: str
    shape: str
    position: tuple
    dims: tuple
    center: tuple
    orientation: str
    axis: Optional[str]
    radius: Optional[float]
    height: Optional[float]
    mass: float
    fragile: bool
    volume: float
    priority: float = 1.0
    scan_shape: Optional[str] = None
    scan_yaw_deg: float = 0.0

    def to_dict(self) -> dict:
        d = {
            "item_id": self.item_id, "shape": self.shape,
            "position": list(self.position), "dims": list(self.dims), "center": list(self.center),
            "orientation": self.orientation, "mass": self.mass, "fragile": self.fragile,
        }
        if self.shape == "cylinder":
            d["axis"] = self.axis
            d["radius"] = self.radius
            d["height"] = self.height
        if self.scan_shape is not None:          # let the frontend draw the real scanned mesh
            d["scan_shape"] = self.scan_shape
            d["scan_yaw_deg"] = self.scan_yaw_deg
        return d


@dataclass
class PackResult:
    strategy: str
    container: Container
    placements: list
    unpacked: list          # [{"id": ..., "reason": ...}]
    metrics: dict
    stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "container": self.container.to_dict(),
            "placements": [p.to_dict() for p in self.placements],
            "unpacked": list(self.unpacked),
            "metrics": self.metrics,
            "stats": self.stats,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=_json_default)


def _json_default(o):
    try:
        import numpy as np
        if isinstance(o, (np.floating, np.integer)):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:  # pragma: no cover
        pass
    if isinstance(o, float) and not math.isfinite(o):
        return None
    raise TypeError(f"not JSON serialisable: {type(o)}")


def validate_items(items) -> list:
    """Return ``list(items)`` after checking types and duplicate ids."""
    out = list(items)
    seen = set()
    for it in out:
        if not isinstance(it, Item):
            raise ValueError(f"expected Item, got {type(it).__name__}")
        if it.id in seen:
            raise ValueError(f"duplicate item id {it.id!r}")
        seen.add(it.id)
    return out
