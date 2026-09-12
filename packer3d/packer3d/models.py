"""Data model: items, containers, obstacles, placements and results.

Coordinates are right-handed: x = length, y = width, z = up, origin at the container's
minimum corner. A cylindrical container is vertical with ``dims = (2R, 2R, H)`` and its
axis at ``(R, R)``.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
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


# --------------------------------------------------------------------------- footprints
# A scanned item's real cross-section: a convex polygon in the item's own horizontal plane
# (SCAN_OUTPUT.md's ``footprint``, at most 16 ``[x, z]`` vertices in metres relative to the
# box centre).  The phone's ``z`` runs along the scan's *depth*, which is packer3d's local
# ``y`` (``dims[1]``), so the pairs are stored here verbatim and read as (local x, local y) --
# a relabel, never a conversion.
#
# The three helpers below mirror ``physics/geometry.py``'s ``convex_hull_2d`` /
# ``polygon_area_2d`` / ``convex_clip_2d`` (same algorithms, same tolerances) on plain tuples
# instead of numpy arrays: these polygons have 3-16 vertices, where array overhead costs more
# than the arithmetic.  Deliberately not imported across that boundary -- packer3d imports
# nothing outside itself (see physics_bridge.py's module docstring).
FOOTPRINT_AREA_EPS = EPS * EPS   # 1e-12 m^2: an intersection smaller than this is a touch


def convex_hull_2d(points) -> tuple:
    """Convex hull of 2D points (Andrew's monotone chain), CCW, no duplicate or collinear
    vertices.  May return 1 or 2 points for degenerate input."""
    pts = sorted({(float(x), float(y)) for x, y in points})
    if len(pts) <= 2:
        return tuple(pts)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return tuple(lower[:-1] + upper[:-1])


def polygon_area_2d(poly) -> float:
    """Shoelace area of a polygon (positive)."""
    n = len(poly)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return abs(s) / 2.0


def convex_clip_2d(subject, clipper) -> list:
    """Sutherland-Hodgman intersection of two CCW convex polygons -> CCW polygon, possibly
    empty."""
    out = list(subject)
    m = len(clipper)
    for i in range(m):
        if not out:
            break
        ax, ay = clipper[i]
        bx, by = clipper[(i + 1) % m]
        ex, ey = bx - ax, by - ay
        nxt = []
        prev = out[-1]
        prev_in = ex * (prev[1] - ay) - ey * (prev[0] - ax) >= -1e-15
        for cur in out:
            cur_in = ex * (cur[1] - ay) - ey * (cur[0] - ax) >= -1e-15
            if cur_in != prev_in:
                dx, dy = cur[0] - prev[0], cur[1] - prev[1]
                den = ex * dy - ey * dx
                if abs(den) < 1e-18:
                    nxt.append(cur)
                else:
                    t = (ex * (ay - prev[1]) - ey * (ax - prev[0])) / den
                    nxt.append((prev[0] + t * dx, prev[1] + t * dy))
            if cur_in:
                nxt.append(cur)
            prev, prev_in = cur, cur_in
        out = nxt
    return out


def footprints_overlap(a, b) -> bool:
    """Do two CCW convex polygons share more than a touching contact?  The ONE overlap rule
    the decoder and ``verify()`` both call, so they can never disagree about a shape."""
    return polygon_area_2d(convex_clip_2d(a, b)) > FOOTPRINT_AREA_EPS


def rect_polygon(lo, hi) -> tuple:
    """The CCW (x, y) rectangle of an axis-aligned box -- what an item with no scanned
    footprint uses, which makes the polygon test identical to today's AABB test for it."""
    return ((lo[0], lo[1]), (hi[0], lo[1]), (hi[0], hi[1]), (lo[0], hi[1]))


def point_in_polygon_2d(p, poly) -> bool:
    """Is ``p`` strictly inside a CCW convex polygon?  A point on an edge counts as outside,
    so a candidate corner flush with a footprint stays alive (the feasibility test judges it)."""
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        if (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax) <= 0.0:
            return False
    return True


def _check_footprint(fp, dims, what: str) -> tuple:
    """Normalise a scanned footprint: hulled (CCW, convex, de-duplicated) and required to be a
    real cross-section that stays inside the bounding box -- the box must remain a conservative
    envelope of the prism, exactly as ``physics.geometry.footprint_local`` requires."""
    pts = []
    for p in fp:
        if len(p) != 2 or not all(is_finite_number(v) for v in p):
            raise ValueError(f"{what}: footprint vertex must be 2 finite numbers, got {p!r}")
        pts.append((float(p[0]), float(p[1])))
    hull = convex_hull_2d(pts)
    if len(hull) < 3 or polygon_area_2d(hull) <= FOOTPRINT_AREA_EPS:
        raise ValueError(f"{what}: footprint is degenerate (needs >= 3 non-collinear vertices)")
    hx, hy = dims[0] / 2.0, dims[1] / 2.0
    if any(abs(x) > hx + EPS or abs(y) > hy + EPS for x, y in hull):
        raise ValueError(f"{what}: footprint exceeds dims {dims!r} in local x/y -- it must be "
                         f"relative to the box centre, within +-({hx}, {hy})")
    return hull


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
    compressibility_k: float = 1.0       # loose volume / squeezed volume; dims are already the squeezed size (see compressed())
    height_grid: Optional[tuple] = None  # heightmap (metres, tuple of tuples), local (x=dims[0], y=dims[1]) frame
    grid_cell: Optional[float] = None    # side length of one height_grid cell, metres
    footprint: Optional[tuple] = None    # convex (x, y) cross-section, metres, relative to the
                                         # box CENTRE, local (x=dims[0], y=dims[1]) frame; see
                                         # the "footprints" section above.  Boxes only.

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
        if self.footprint is not None:
            if self.shape != "box":
                raise ValueError(f"item {self.id}: only a box carries a footprint; a cylinder's "
                                 f"cross-section is its circle (shape={self.shape!r})")
            object.__setattr__(self, "footprint",
                               _check_footprint(self.footprint, self.dims, f"item {self.id}"))

    # ---- constructors -------------------------------------------------
    @staticmethod
    def box(id: str, length: float, width: float, height: float, mass: float = 0.0, *,
            fragile: bool = False, keep_upright: bool = False, priority: float = 1.0,
            footprint=None) -> "Item":
        return Item(id, "box", (length, width, height), mass, fragile, keep_upright, True, priority,
                    footprint=footprint)

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
                  fragile: Optional[bool] = None, keep_upright: Optional[bool] = None,
                  allow_lay_down: bool = True, priority: float = 1.0) -> "Item":
        """Build an item from a lidar measurement: bounding dims (length, depth, height) + shape.

        ``shape`` is "box" or "cylinder" (case-insensitive; "cuboid"/"cube" and "cyl"/"can"/"bottle"
        also accepted).  Any other shape (oval, trapezoid, mannequin, ...) is packed as its bounding
        box, and defaults to ``fragile=True`` (nothing may rest on a non-flat top) and
        ``keep_upright=True`` (an unrecognized/irregular scan is never deliberately tipped onto
        its side by the solver -- both defaults can be overridden explicitly).
        A cylinder is assumed to stand along its height, radius = min(length, depth)/2.
        Mass is unknown to the scanner; leave it 0 and the CoM falls back to the volume centroid,
        or pass a measured/estimated mass.
        """
        kind = str(shape).strip().lower()
        if kind in ("box", "cuboid", "cube", "rect", "rectangular"):
            return Item.box(id, length, depth, height, mass, fragile=bool(fragile), keep_upright=bool(keep_upright),
                            priority=priority)
        if kind in ("cylinder", "cyl", "can", "bottle", "tube", "round"):
            _check_positive(length, f"item {id} length")
            _check_positive(depth, f"item {id} depth")
            return Item.cylinder(id, min(length, depth) / 2.0, height, mass, fragile=bool(fragile),
                                 keep_upright=bool(keep_upright), allow_lay_down=allow_lay_down, priority=priority)
        # anything else (oval, trapezoid, mannequin, ...) packs as its bounding box; its top is
        # not flat (fragile=True by default) and it's never deliberately tipped over (keep_upright=True).
        it = Item.box(id, length, depth, height, mass, fragile=True if fragile is None else fragile,
                      keep_upright=True if keep_upright is None else keep_upright, priority=priority)
        object.__setattr__(it, "scan_shape", kind or "irregular")
        return it

    @staticmethod
    def from_mesh(id: str, vertices, faces=None, mass: float = 0.0, *, fragile: Optional[bool] = None,
                  keep_upright: Optional[bool] = None, allow_lay_down: bool = True, priority: float = 1.0,
                  yaw_search_deg: float = 1.0, classify: bool = True) -> "Item":
        """Build an item from a scanned mesh (``vertices`` (N,3), optional triangle ``faces`` (M,3)).

        * The mesh is rotated about z (0..90 deg in ``yaw_search_deg`` steps) to find the smallest
          footprint; the chosen yaw is stored in ``scan_yaw_deg`` for the frontend.
        * With faces, the closed-mesh volume is measured and the shape is classified:
          volume/bbox ~ 1 -> box, ~ pi/4 with a square footprint -> cylinder, otherwise irregular.
          ``classify=False`` skips detection and always treats the mesh as irregular.
        * Irregular objects pack as their bounding box and default to both ``fragile=True``
          (their top is not flat, so nothing is stacked on them) and ``keep_upright=True`` (the
          solver never deliberately tips an unrecognized shape onto its side) -- override either
          explicitly if you know better.
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
            it = Item.box(id, dims[0], dims[1], dims[2], mass, fragile=bool(fragile), keep_upright=bool(keep_upright),
                          priority=priority)
        elif kind == "cylinder":
            it = Item.cylinder(id, min(dims[0], dims[1]) / 2.0, dims[2], mass, fragile=bool(fragile),
                               keep_upright=bool(keep_upright), allow_lay_down=allow_lay_down, priority=priority)
        else:
            it = Item.box(id, dims[0], dims[1], dims[2], mass, fragile=True if fragile is None else fragile,
                          keep_upright=True if keep_upright is None else keep_upright, priority=priority)
        object.__setattr__(it, "scan_shape", kind)
        object.__setattr__(it, "scan_yaw_deg", best_yaw)
        if vol is not None:
            object.__setattr__(it, "true_volume", float(vol))
        return it

    @staticmethod
    def from_scanned_heightmap(data: dict, mass: float = 0.0, *, units: str = "cm",
                               fragile: Optional[bool] = None, keep_upright: Optional[bool] = None,
                               allow_lay_down: bool = True, priority: float = 1.0,
                               classify: bool = True) -> "Item":
        """Build an item from the LiDAR spike's scan JSON (see ``SCAN_OUTPUT.md``):
        ``{"id","width","depth","height","cellSize","heights"}``, all in centimetres by default
        (pass ``units="m"`` if already converted).  ``heights[i][j]`` is the object's real surface
        height at that footprint cell (0 = nothing there) -- an open shoe, an L-shaped bracket, or
        a hole through the middle all show up here, unlike a plain bounding box.

        Classification, using the heightmap (falls back to "box" with no ``heights``, or with
        ``classify=False``):
          * footprint mostly filled and volume ~ the full box -> **box**
          * roughly square footprint with a circular fill fraction (~ pi/4) -> **cylinder**
          * otherwise -> **irregular** (packed as the sub-boxes its grid carves, defaulting to
            ``keep_upright=True`` -- never deliberately tipped onto its side by the solver;
            override explicitly).  NOT ``fragile`` by default, unlike ``from_scan``/``from_mesh``,
            which have no grid: here the grid itself says where every surface is, and a blanket
            "nothing may rest on it" would also forbid resting anything in the item's own cavity
            (see the comment at the construction below).
        Real (heightmap-integrated) volume, not the bounding-box volume, is stored as
        ``true_volume`` and used for utilisation metrics.

        Raises ``ValueError`` if required keys (``id``, ``width``, ``depth``, ``height``) are
        missing, if ``heights`` is a ragged (non-rectangular) grid, or if any value is
        non-finite -- rather than letting a raw ``KeyError``/numpy error leak out.
        """
        import numpy as np
        if "dimensions" in data and "width" not in data:  # server document form: [width, height, depth] in metres
            w, h, dp = data["dimensions"]
            data, units = {**data, "width": w, "height": h, "depth": dp}, "m"
        for key in ("id", "width", "depth", "height"):
            if key not in data:
                raise ValueError(f"scan payload is missing required key {key!r}: {data!r}")
        scale = 0.01 if units == "cm" else 1.0
        iid = str(data["id"])
        width = float(data["width"]) * scale
        depth = float(data["depth"]) * scale
        height = float(data["height"]) * scale
        heights = data.get("heights")
        cell = float(data.get("cellSize", 0.0)) * scale
        if not is_finite_number(width) or width <= 0:
            raise ValueError(f"scan {iid}: width must be a finite number > 0, got {data['width']!r}")
        if not is_finite_number(depth) or depth <= 0:
            raise ValueError(f"scan {iid}: depth must be a finite number > 0, got {data['depth']!r}")
        if not is_finite_number(height) or height <= 0:
            raise ValueError(f"scan {iid}: height must be a finite number > 0, got {data['height']!r}")
        kind, vol = "box", None
        if classify and heights:
            row_lens = {len(row) for row in heights}
            if len(row_lens) > 1:
                raise ValueError(f"scan {iid}: ragged heightmap -- rows have lengths {sorted(row_lens)}")
            arr = np.asarray(heights, dtype=float) * scale
            if not np.all(np.isfinite(arr)):
                raise ValueError(f"scan {iid}: heightmap contains non-finite values")
            if np.any(arr < -EPS):
                raise ValueError(f"scan {iid}: heightmap contains negative heights")
            rows, cols = arr.shape if arr.ndim == 2 else (0, 0)
            n = rows * cols
            if n > 0 and cell > 0:
                vol = float(arr.sum()) * cell * cell
                fill_frac = float((arr > 1e-9).sum()) / n
                bbox_vol = width * depth * height
                ratio = (vol / bbox_vol) if bbox_vol > 0 else 0.0
                square = abs(width - depth) <= 0.08 * max(width, depth, 1e-9)
                if ratio >= 0.9 and fill_frac >= 0.9:
                    kind = "box"
                elif square and abs(fill_frac - math.pi / 4) <= 0.12:
                    kind = "cylinder"
                else:
                    kind = "irregular"
        # `footprint` (SCAN_OUTPUT.md): [x, z] pairs about the box centre, z along the scan's
        # depth == packer3d's local y.  Same units as the dims, so the same `scale`.
        fp = data.get("footprint")
        if fp is not None:
            fp = [(float(x) * scale, float(z) * scale) for x, z in fp]
        if kind == "cylinder":
            it = Item.cylinder(iid, min(width, depth) / 2.0, height, mass, fragile=bool(fragile),
                               keep_upright=bool(keep_upright), allow_lay_down=allow_lay_down, priority=priority)
        else:
            # NOT fragile by default, even for an "irregular" scan.  ``fragile`` here would be a
            # statement about the SHAPE ("the top is not flat, do not stack blind"), and the
            # height grid already says exactly where every surface is -- while the decoder's
            # fragile-below rule refuses to rest anything on ANY of the item's solids, the floor
            # of its own cavity included.  Since a genuine cavity is what makes a scan irregular
            # in the first place (volume/bbox < 0.9), those two defaults together meant no real
            # solve could ever nest anything, in the one place resting is intended (FIXES.md
            # section 4, 2026-09-12).  An object that really does break says so through
            # ``rigidity: "fragile"`` -> ``physics.prepack`` -> an explicit ``fragile=True``
            # here, and that still protects every one of its surfaces.
            # ``keep_upright`` keeps its irregular default: an unrecognised scan is still never
            # deliberately tipped onto its side.
            it = Item.box(iid, width, depth, height, mass, fragile=bool(fragile),
                          keep_upright=(kind == "irregular" if keep_upright is None else bool(keep_upright)),
                          priority=priority, footprint=fp)
        object.__setattr__(it, "scan_shape", kind)
        if vol is not None:
            object.__setattr__(it, "true_volume", float(vol))
            object.__setattr__(it, "height_grid", tuple(tuple(row) for row in arr.tolist()))
            object.__setattr__(it, "grid_cell", cell)
        return it

    def solid_boxes(self, max_blocks: int = 4) -> list:
        """Axis-aligned boxes (local ``(lo, hi)`` tuples, item's own x/y/z frame) that
        approximate the scanned shape, instead of treating the whole bounding box as solid.

        Built by max-pooling ``height_grid`` down to at most ``max_blocks`` x ``max_blocks``
        cells (bounds how many solids one item turns into) and emitting one box per cell, from
        the floor up to that cell's tallest point. Max-pooling (not averaging) is deliberate:
        it can only overstate a cell's height, never carve out cavity that isn't really there.

        A cell at height ~0 gets no box at all -- a real hole clear through the item, open for
        gravity or a smaller item's bounding box to occupy (an open shoe's throat, a bowl's
        interior). ``height_grid`` uses the scanner's own convention (SCAN_OUTPUT.md): ``0``
        means "nothing observed here", which does not distinguish a genuine gap from an
        occluded-but-solid interior cell -- we take it at face value and treat it as empty,
        same as the scanner's contract for every other consumer of this field.

        Falls back to the plain bounding box when there's no height grid (a primitive box/
        cylinder, or a scan built with ``classify=False``).

        THE CEILING THIS PUTS ON NESTING (measured 2026-09-12; don't rediscover it).  The pool is
        4x4 whatever ``cellSize`` is, so a cavity is always a whole pooled block: a recess that
        isn't block-aligned is pooled away entirely and the item ends up with NO cavity, even
        though the full grid still counts it in ``true_volume``/the classifier.  Of the 16 cells,
        two recessed blocks adjacent along an axis read as one recess spanning both, and index 0
        or 3 touches the item's own wall (an open notch, not a rimmed cut-out), so only indices 1
        and 2 are interior per axis -- and they are adjacent.  The only arrangement that yields
        two fully rimmed cavity cells is the diagonal pair (1,1)/(2,2), meeting along one corner
        line; **three rimmed cells cannot be expressed through a scan document at all**, at any
        resolution.  A camera case with three foam cut-outs is out of reach.

        Why it stays 4x4 anyway: ``tools/packbench/packbench.py`` at a fixed iteration cap,
        pool 4x4 -> 6x6 -> 8x8, is 32 -> 72 -> 96 sub-boxes for two grid items and costs
        5.9 -> 9.7 s (carryon_weekend) and 9.1 -> 15.0 -> 24.6 s (checked_heavy_light): +65% per
        step and +170% at 8x8, for ZERO extra items packed on the corpus (8x8 bought 1.6 points
        of utilisation on one fixture).  And ``physics.packer3d_adapter._cavity_local_boxes``
        duplicates this pooling on the other side of the JSON boundary with its own
        ``max_blocks=4``: raising it here alone would make the gate grade a shape the solver
        never packed, which is the exact class of mismatch that has bitten nesting twice today.
        A shoe or a dopp kit has one cavity, so the cap costs nothing real yet -- raise both
        sides together, or nothing.
        """
        if self.height_grid is None:
            return [((0.0, 0.0, 0.0), self.dims)]
        import numpy as np
        arr = np.asarray(self.height_grid, dtype=float)
        dx, dy, dz = self.dims
        rows, cols = arr.shape
        row_blocks = np.array_split(np.arange(rows), min(max_blocks, rows))
        col_blocks = np.array_split(np.arange(cols), min(max_blocks, cols))
        boxes = []
        x0 = 0.0
        for ri in row_blocks:
            x1 = x0 + len(ri) / rows * dx
            y0 = 0.0
            for cj in col_blocks:
                y1 = y0 + len(cj) / cols * dy
                h = float(arr[np.ix_(ri, cj)].max())
                if h > EPS:
                    boxes.append(((x0, y0, 0.0), (x1, y1, min(h, dz))))
                y0 = y1
            x0 = x1
        return boxes or [((0.0, 0.0, 0.0), self.dims)]

    def compressed(self, k: float) -> "Item":
        """Copy of this item at its squeezed size: ``k`` = loose volume / squeezed volume (>= 1).

        Height (item z) is divided by ``k`` so the item takes ``k`` times less space, the way
        clothes fold flat and squash down. ``k == 1`` returns ``self`` unchanged.
        """
        k = float(k)
        if not is_finite_number(k) or k < 1.0:
            raise ValueError(f"item {self.id}: compressibility_k must be >= 1, got {k!r}")
        if k == 1.0:
            return self
        # ponytail: squashes along height only; go isotropic (each dim / k**(1/3)) if items ever need it.
        d = self.dims
        return replace(self, dims=(d[0], d[1], d[2] / k), compressibility_k=k,
                       height=None if self.height is None else self.height / k,
                       true_volume=None if self.true_volume is None else self.true_volume / k,
                       height_grid=None if self.height_grid is None
                       else tuple(tuple(v / k for v in row) for row in self.height_grid))

    # ---- derived ------------------------------------------------------
    @property
    def bbox_volume(self) -> float:
        return self.dims[0] * self.dims[1] * self.dims[2]

    @property
    def prism_volume(self) -> Optional[float]:
        """Footprint area x height, or None without a scanned footprint."""
        if self.footprint is None:
            return None
        return polygon_area_2d(self.footprint) * self.dims[2]

    @property
    def volume(self) -> float:
        """True volume (mesh volume if scanned, pi r^2 h for cylinders, the footprint prism
        for a scanned hull, else the box)."""
        if self.true_volume is not None:
            return min(self.true_volume, self.bbox_volume)
        if self.shape == "cylinder":
            return math.pi * self.radius ** 2 * self.height
        if self.footprint is not None:
            return min(self.prism_volume, self.bbox_volume)
        return self.bbox_volume

    @property
    def occupied_volume(self) -> float:
        """How much space this item really takes: ``bbox_volume`` for a plain box, less when a
        height grid carves a cavity or a footprint cuts the cross-section (the true solid is
        inside both, so the smallest estimate is still conservative). The decoder's capacity
        pre-check must use this, not ``bbox_volume``: an item with a real cavity or a narrow
        footprint can leave a container with usable space even though its bounding box alone
        would appear to fill it."""
        if self.height_grid is None and self.footprint is None:
            return self.bbox_volume
        vols = [self.bbox_volume]
        if self.footprint is not None:
            vols.append(self.prism_volume)
        if self.height_grid is not None:
            vols.append(sum((hi[0] - lo[0]) * (hi[1] - lo[1]) * (hi[2] - lo[2])
                            for lo, hi in self.solid_boxes()))
        return min(vols)

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
        """Static check: some legal orientation fits inside the empty container.

        An upright cylinder in a cylindrical container only has to clear the bore
        (2r <= 2R); ``fits_dims`` would make it clear the diagonal of its bounding
        square instead, which rejects anything wider than R * sqrt(2). A cylinder
        laid on its side really does sweep a rectangle, so it keeps that test.
        """
        for o in self.orientations():
            if container.fits_dims(o.dims):
                return True
            if (container.shape == "cylinder" and o.axis == "z"
                    and all(o.dims[k] <= container.dims[k] + EPS for k in range(3))):
                return True
        return False


def oriented_solid_boxes(item: Item, position, dims, orientation_name: str) -> list:
    """World-frame ``(lo, hi)`` boxes occupied by ``item`` placed with its bounding box's min
    corner at ``position`` and oriented dims ``dims`` under orientation ``orientation_name``.

    Uses ``item.solid_boxes()`` (the height-grid decomposition) only when it's meaningful:
    a box-shaped item with a height grid, in one of the two orientations that keep the grid's
    "up" pointing along world z (``"xyz"``/``"yxz"`` -- the only two of the six box permutations
    with ``perm[2] == 2``). Both permutations are pure axis swaps (no reflection), so a local
    box corner maps to world via ``world[k] = position[k] + local[perm[k]]``. Everything else
    (cylinders, an item tipped onto its side) packs/verifies as the plain bounding box, since
    the grid's own up axis is no longer world-up there.
    """
    lo = tuple(float(v) for v in position)
    if item.shape == "box" and item.height_grid is not None and orientation_name in ("xyz", "yxz"):
        perm = BOX_ORIENTATIONS[orientation_name]
        out = []
        for blo, bhi in item.solid_boxes():
            wlo = tuple(lo[k] + blo[perm[k]] for k in range(3))
            whi = tuple(lo[k] + bhi[perm[k]] for k in range(3))
            out.append((wlo, whi))
        return out
    hi = tuple(lo[k] + dims[k] for k in range(3))
    return [(lo, hi)]


def local_footprint_offsets(item: Item, dims, orientation_name: str) -> Optional[tuple]:
    """``item.footprint`` as (x, y) offsets from the placed bounding box's MIN corner, CCW, or
    None when this placement has no usable footprint.

    Same gate as the height grid in ``oriented_solid_boxes``: only the two box orientations
    that keep the item's own z pointing along world z (``"xyz"``/``"yxz"``).  Tipped onto its
    side, a scanned horizontal cross-section describes nothing horizontal any more, so the
    placement falls back to its bounding box.  ``physics.packer3d_adapter._FOOTPRINT_XZ`` gates
    the physics prism on exactly these two orientations and maps the points the same way
    (item-local ``(fx, fz)`` -> packer world ``(fx, fz)`` under "xyz", ``(fz, fx)`` under
    "yxz"); honouring the footprint in more orientations than the gate does gets correct
    placements rejected, in fewer loses packing -- keep the two in step.
    """
    fp = item.footprint if item is not None else None
    if fp is None or item.shape != "box" or orientation_name not in ("xyz", "yxz"):
        return None
    hx, hy = dims[0] / 2.0, dims[1] / 2.0
    if orientation_name == "xyz":
        return tuple((hx + fx, hy + fy) for fx, fy in fp)
    # "yxz" puts the item's own y on world x and vice versa; that swap mirrors the polygon, so
    # walk it backwards to keep the winding CCW for convex_clip_2d.
    return tuple((hx + fy, hy + fx) for fx, fy in reversed(fp))


def oriented_footprint(item: Item, position, dims, orientation_name: str) -> Optional[tuple]:
    """World-frame (x, y) convex polygon of a placement's scanned cross-section, or None.
    The decoder offsets ``local_footprint_offsets`` by a candidate corner with the same
    expression, so its candidate polygons are bit-identical to what ``verify()`` rebuilds."""
    off = local_footprint_offsets(item, dims, orientation_name)
    if off is None:
        return None
    x0, y0 = float(position[0]), float(position[1])
    return tuple((x0 + ox, y0 + oy) for ox, oy in off)


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
    # Set by the decoder when it placed this item inside another item's scanned cavity:
    # ``{"item_id": host, "cavity": [x, y, z, dx, dy, dz]}`` (min corner + extents, same frame
    # as ``position``/``dims``).  ``None`` for an ordinary placement.  Consumers that model an
    # item as one bounding box need it to tell a legal nest from a collision.
    nested_in: Optional[dict] = None

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
        if self.nested_in is not None:           # omitted entirely for an un-nested placement
            # Wire form is `position` + `dims`, the same spelling a placement uses, because that
            # is what `server/app_plan.py::_nesting` reads to build the plan JSON's `nestedIn`.
            # Internally the cavity travels as one 6-list; only the serialised shape splits it.
            cav = list(self.nested_in["cavity"])
            d["nested_in"] = {"item_id": self.nested_in["item_id"],
                              "position": cav[:3], "dims": cav[3:]}
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
