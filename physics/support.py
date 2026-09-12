"""Static stability heuristic for packed objects.

This is a STATIC STABILITY HEURISTIC, NOT a full rigid-body simulator. It does
not integrate forces/torques over time, does not model friction, and does not
predict dynamic tipping under acceleration (braking, turning, being dropped).
It answers one narrower question: "given these final resting poses, does each
object look adequately supported and balanced under gravity alone?"

Model (geometrically exact for boxes, unlike the bounding-rectangle
approximation this replaced):
- Rigid bodies, uniform density per object, so an object's center of mass is
  its OBB center (`SceneGeometry.centers[i]`); its COM projection is that
  center's (X, Z).
- Gravity acts along -Y (Y is up, per physics.schema).
- Contact footprints are exact convex polygons in the XZ plane. An object's
  bottom contact set is the subset of its 8 world-space corners with
  `y <= aabb_min.y + epsilon`; the bottom footprint is the 2D convex hull
  (monotone chain) of those corners projected to XZ -- 4 points for a
  face-down box at any yaw, 2 for edge contact, 1 for corner contact. A
  supporter's top contact set is likewise the corners with
  `y >= aabb_max.y - epsilon`. The container floor is a single flat plane at
  `container_floor_y` whose footprint is the XZ hull of the container's 4
  lowest corners (so a yaw-rotated or mildly tilted container still works; a
  heavily tilted container, where "the floor" is no longer one horizontal
  plane, is out of scope).
- A support patch is the convex polygon intersection (Sutherland-Hodgman) of
  the object's bottom footprint with each supporter's top footprint, for every
  supporter reported by `scene_geometry.resting_pairs` plus the container floor
  when `scene_geometry.on_floor`. Patch areas come from the shoelace formula.
- PATCHES-ARE-DISJOINT ASSUMPTION: `covered_area = sum(patch areas)`, clamped
  to the bottom footprint area. This is exact for the collision-free scenes
  this layer is given: two supporters that touch the same object at the same
  height cannot overlap each other in XZ without interpenetrating. In a scene
  that is already colliding (validated separately) overlapping supporters
  would be double-counted, which the clamp caps at ratio 1.0.
- DEGENERATE CONTACT: when the bottom footprint has area < 1e-9 m^2 (edge or
  corner contact -- a hull that is a segment or a single point), area ratios
  are meaningless, so `support_ratio` is instead the fraction of the bottom
  contact points that lie inside, or within 1e-9 m of, some supporter's top
  footprint (floor included). So a box balanced on one bottom edge whose two
  contact corners both land on a supporter reports 1.0, one corner reports 0.5.
- STABILITY: the support polygon is the convex hull of the union of all support
  patch vertices, and `stability_margin_m` is the signed 2D distance from the
  COM projection to the nearest point of that hull's boundary -- positive
  inside, negative outside (the standard static criterion: stable iff the COM
  projects inside the support polygon). When the support polygon degenerates to
  a segment or a point (edge/corner contact) there is no inside, so the margin
  is minus the distance to it. TIE-BREAK: a margin in (-1e-9, 0) is snapped to
  exactly 0.0, so a COM sitting on the boundary of the support polygon -- the
  perfectly edge-balanced box -- reads as margin 0.0 and `unstable = False`
  (`unstable` is `margin < 0`). Boundary counts as supported; float noise does
  not decide the verdict.
- No support at all (empty support polygon) reports
  `stability_margin_m = FLOATING_MARGIN_SENTINEL_M` rather than a real
  distance.
- BRIDGING: the support polygon is a hull, so it spans the void between two
  separated supporters -- correct rigid-body statics (a plank on two bricks
  with its COM between them does not topple) but it hides a real-world risk,
  because the void's "supporters" out here are shoes and soft bags that sag.
  `com_over_patch` is the extra bit: True when the COM projection lies inside
  (or within `TOUCH_TOL_M` of) at least ONE individual contact patch, False
  when it is only inside the hull of several. So a laptop bridging two shoes
  reads `unstable=False, com_over_patch=False` -- statically fine, resting on
  nothing. `unstable and not com_over_patch` is just an overhang; the
  interesting case is `not unstable and not com_over_patch`.
- CHAIN INSTABILITY: `supported_by_unstable` is True when any object this one
  rests on is itself `floating`, `unstable`, or `supported_by_unstable`.
  Computed bottom-up in ascending `aabb_min.y` order (a supporter always has a
  lower bottom than the object resting on it), so "A destabilizes B
  destabilizes C" propagates up the whole stack.

Complexity: O(n) hull precompute, plus `resting_pairs`'s vectorized O(n^2)
height/AABB test and a polygon clip per surviving (object, supporter) pair --
a handful per object in practice. Fine for hackathon scale (5-20 objects).

Fast paths (pure speed; every one of them is required to return exactly what
the general code would, bit for bit): the contact masks for all objects are one
vectorized comparison; when a contact set is the 4 corners of one box face
(the common face-down case at any yaw) its XZ hull is those 4 corners in known
cyclic order, so the monotone chain is skipped -- but only when the projected
face is strictly convex, otherwise `_hull` runs; and when an object has exactly
one support patch equal to its own bottom footprint (it sits wholly inside one
supporter, e.g. the floor) that footprint already IS the support polygon.

Known failure modes / out of scope:
- No friction: a box that would obviously be held by friction against a wall
  or a neighbor is judged on footprint/COM alone.
- No dynamics: no acceleration, vibration, impact or toppling simulation; no
  normal-force distribution is solved, so a supported-but-overloaded shelf
  looks the same as a sound one (mass only reaches the constraints layer).
- Uniform density: the COM is the geometric center, not a real mass
  distribution.
- Single flat floor plane; a heavily tilted container is out of scope.
- Patches assumed disjoint (see above).
- Rigid bodies: `rigidity`/`compressibility_k` are not consulted here, so a
  squashed soft item's real (larger) contact patch is not modelled.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from physics.geometry import SIGNS
from physics.scene_geometry import SceneGeometry, on_floor, precompute, resting_pairs
from physics.schema import Scene

FLOATING_MARGIN_SENTINEL_M = -1.0e6
# Hull areas below this are an edge/corner contact, not a face contact.
DEGENERATE_AREA_M2 = 1e-9
# "On the boundary" tolerance: point-in-polygon slack and the margin tie-break.
TOUCH_TOL_M = 1e-9

Pt = tuple[float, float]  # (x, z)


@dataclass
class SupportResult:
    object_id: str
    support_ratio: float
    stability_margin_m: float
    supporting_objects: list[str] = field(default_factory=list)
    floating: bool = False
    unstable: bool = False
    # True if anything this object rests on is floating/unstable/itself
    # standing on something unstable (chain reaction).
    supported_by_unstable: bool = False
    # Support polygon (convex hull of all support patches) as [x, z] pairs,
    # empty when unsupported. Renderer diagnostic.
    contact_polygon: list[list[float]] = field(default_factory=list)
    # supporter id ("container_floor" included) -> contact patch area m^2.
    patch_areas_m2: dict[str, float] = field(default_factory=dict)
    # True when the COM projects into one actual contact patch, not merely into
    # the hull of several. False + unstable=False == bridging a void.
    com_over_patch: bool = False


def _cross(o: Pt, a: Pt, b: Pt) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _hull(pts: list[Pt]) -> list[Pt]:
    """Convex hull of XZ points (monotone chain), CCW, collinear points
    dropped. Returns 1 or 2 points for a degenerate (point / segment) set."""
    pts = sorted(set(pts))
    if len(pts) <= 2:
        return pts

    def half(seq: list[Pt]) -> list[Pt]:
        out: list[Pt] = []
        for p in seq:
            while len(out) >= 2 and _cross(out[-2], out[-1], p) <= 0.0:
                out.pop()
            out.append(p)
        return out

    lower = half(pts)
    upper = half(pts[::-1])
    hull = lower[:-1] + upper[:-1]
    return hull if len(hull) >= 3 else [pts[0], pts[-1]]


def _face_cycles() -> dict[int, tuple[int, int, int, int]]:
    """corner-index bitmask -> that set's 4 corners in cyclic (rectangle) order.

    One entry per box face, derived from `geometry.SIGNS` so it cannot drift
    from the corner order `SceneGeometry.vertices` actually uses. A face is the
    4 corners sharing one local axis sign; walking the other two axes'
    sign pairs as (-,-) (-,+) (+,+) (+,-) traverses the rectangle, not its
    diagonal.
    """
    cycles: dict[int, tuple[int, int, int, int]] = {}
    for axis in range(3):
        b, c = (k for k in range(3) if k != axis)
        for sign in (-1.0, 1.0):
            rows = [i for i in range(8) if SIGNS[i, axis] == sign]
            cycle = tuple(
                next(i for i in rows if SIGNS[i, b] == sb and SIGNS[i, c] == sc)
                for sb, sc in ((-1.0, -1.0), (-1.0, 1.0), (1.0, 1.0), (1.0, -1.0))
            )
            cycles[sum(1 << i for i in cycle)] = cycle
    return cycles


_FACE_CYCLES = _face_cycles()
# bitmask -> set corner indices, ascending (same order as a boolean-mask select).
_BIT_INDICES = {m: tuple(k for k in range(8) if m >> k & 1) for m in range(256)}
_BIT_WEIGHTS = (1 << np.arange(8)).astype(np.int64)


def _face_hull(xz: list[Pt], cycle: tuple[int, int, int, int] | None) -> list[Pt] | None:
    """`_hull` of the 4 corners of one box face, without the monotone chain.

    `xz` is one object's 8 corners projected to (x, z) in SIGNS order, `cycle`
    the face's corners in cyclic order (from `_FACE_CYCLES`, None for any other
    contact set). A box face projects to a parallelogram, so when it is
    strictly convex its hull is exactly those 4 points -- emitted CCW and
    rotated to start at the lexicographically smallest one, which is precisely
    what `_hull` returns. Returns None (-> caller falls back to `_hull`) for
    anything else: a non-face contact set, or a projection that is a segment or
    a point (vertical face, coincident corners) where the chain's collinear
    handling has to decide.
    """
    if cycle is None:
        return None
    p0, p1, p2, p3 = xz[cycle[0]], xz[cycle[1]], xz[cycle[2]], xz[cycle[3]]
    x0, z0 = p0
    x1, z1 = p1
    x2, z2 = p2
    x3, z3 = p3
    t0 = (x1 - x0) * (z2 - z0) - (z1 - z0) * (x2 - x0)
    t1 = (x2 - x1) * (z3 - z1) - (z2 - z1) * (x3 - x1)
    t2 = (x3 - x2) * (z0 - z2) - (z3 - z2) * (x0 - x2)
    t3 = (x0 - x3) * (z1 - z3) - (z0 - z3) * (x1 - x3)
    if t0 > 0.0 and t1 > 0.0 and t2 > 0.0 and t3 > 0.0:
        quad = [p0, p1, p2, p3]
    elif t0 < 0.0 and t1 < 0.0 and t2 < 0.0 and t3 < 0.0:
        quad = [p3, p2, p1, p0]
    else:
        return None  # collinear / degenerate projection: let _hull decide
    start = quad.index(min(quad))
    return quad[start:] + quad[:start]


def _poly_area(poly: list[Pt]) -> float:
    """Shoelace area (unsigned). 0.0 for a segment or point."""
    k = len(poly)
    if k < 3:
        return 0.0
    # Term order (0..k-2, then the wrap-around term) is the summation order of
    # the textbook loop -- float addition is not associative, so it is kept.
    s = 0.0
    x1, z1 = poly[0]
    for i in range(1, k):
        x2, z2 = poly[i]
        s += x1 * z2 - x2 * z1
        x1, z1 = x2, z2
    x2, z2 = poly[0]
    return abs(s + (x1 * z2 - x2 * z1)) / 2.0


def _seg_dist(p: Pt, a: Pt, b: Pt) -> float:
    dx, dz = b[0] - a[0], b[1] - a[1]
    d2 = dx * dx + dz * dz
    t = 0.0 if d2 <= 0.0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dz) / d2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dz))


def _signed_dist(p: Pt, poly: list[Pt]) -> float:
    """Signed distance from `p` to convex `poly` (CCW): + inside, - outside,
    magnitude = distance to the boundary. Degenerate polys (segment, point)
    have no inside, so the result is always <= 0."""
    if not poly:
        return FLOATING_MARGIN_SENTINEL_M
    if len(poly) == 1:
        return -math.hypot(p[0] - poly[0][0], p[1] - poly[0][1])
    if len(poly) == 2:
        return -_seg_dist(p, poly[0], poly[1])
    # `_cross(a, b, p)` and `_seg_dist(p, a, b)` inlined and sharing their
    # subexpressions: same operands, same operation order, same bits.
    px, pz = p
    inside = True
    dist = math.inf
    k = len(poly)
    for i in range(k):
        ax, az = poly[i]
        b = poly[i + 1] if i + 1 < k else poly[0]
        dx, dz = b[0] - ax, b[1] - az
        rx, rz = px - ax, pz - az
        if dx * rz - dz * rx < 0.0:
            inside = False
        d2 = dx * dx + dz * dz
        t = 0.0 if d2 <= 0.0 else max(0.0, min(1.0, (rx * dx + rz * dz) / d2))
        d = math.hypot(px - (ax + t * dx), pz - (az + t * dz))
        if d < dist:
            dist = d
    return dist if inside else -dist


def _clip(subject: list[Pt], clipper: list[Pt]) -> list[Pt]:
    """Sutherland-Hodgman: `subject` clipped by convex CCW `clipper`.

    `subject` may be degenerate (a 2-point segment or a single point): the
    wrap-around edge list handles both, so edge/corner contacts clip correctly.
    A degenerate *clipper* (a supporter whose own top contact is an edge or a
    corner) has no interior to clip against, so the patch is just the subject
    points lying on it -- zero area either way."""
    if not subject or not clipper:
        return []
    if len(clipper) < 3:
        return [p for p in subject if _signed_dist(p, clipper) >= -TOUCH_TOL_M]
    out = list(subject)
    k = len(clipper)
    for i in range(k):
        if not out:
            return []
        # Edge order is the clip order and the clip order shapes the output
        # polygon, so it stays (clipper[i] -> clipper[i+1]), wrapping at the end.
        a = clipper[i]
        b = clipper[i + 1] if i + 1 < k else clipper[0]
        ax, az = a
        ex, ez = b[0] - ax, b[1] - az  # _cross(a, b, p) == ex*(pz-az) - ez*(px-ax)
        if all(ex * (p[1] - az) - ez * (p[0] - ax) >= 0.0 for p in out):
            continue  # nothing to cut (the common "sits well inside" case)
        nxt: list[Pt] = []
        prev = out[-1]
        prev_in = ex * (prev[1] - az) - ez * (prev[0] - ax) >= 0.0
        for cur in out:
            cur_in = ex * (cur[1] - az) - ez * (cur[0] - ax) >= 0.0
            if cur_in != prev_in:
                nxt.append(_line_isect(prev, cur, a, b))
            if cur_in:
                nxt.append(cur)
            prev, prev_in = cur, cur_in
        out = _dedupe(nxt)
    return out


def _line_isect(p: Pt, q: Pt, a: Pt, b: Pt) -> Pt:
    """Intersection of segment p->q with the infinite line a->b (they cross by
    construction, so the denominator is non-zero)."""
    r = (q[0] - p[0], q[1] - p[1])
    s = (b[0] - a[0], b[1] - a[1])
    denom = r[0] * s[1] - r[1] * s[0]
    t = ((a[0] - p[0]) * s[1] - (a[1] - p[1]) * s[0]) / denom
    return (p[0] + t * r[0], p[1] + t * r[1])


def _dedupe(poly: list[Pt]) -> list[Pt]:
    """Drop points coincident with their predecessor (and the wrap-around
    duplicate) -- Sutherland-Hodgman emits those for degenerate subjects."""
    out: list[Pt] = []
    for p in poly:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > 1e-12:
            out.append(p)
    if len(out) > 1 and math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) <= 1e-12:
        out.pop()
    return out


def _xz_hull(points: np.ndarray) -> list[Pt]:
    return _hull([(float(p[0]), float(p[2])) for p in points])


def _axis_rect(hull: list[Pt]) -> tuple[float, float, float, float] | None:
    """(x_min, x_max, z_min, z_max) if `hull` is an axis-aligned rectangle
    (exactly two distinct x and two distinct z values), else None.

    For such a clipper, "every subject point is inside" is exactly "the
    subject's XZ bounding box is inside this rectangle", which is how the
    caller skips `_clip` for the overwhelmingly common object-sitting-well-
    inside-the-floor case."""
    if len(hull) != 4:
        return None
    xs = {p[0] for p in hull}
    zs = {p[1] for p in hull}
    if len(xs) != 2 or len(zs) != 2:
        return None
    return (min(xs), max(xs), min(zs), max(zs))


def check_support(
    scene: Scene,
    epsilon: float = 1e-3,
    floating_threshold: float = 0.05,
    geom: SceneGeometry | None = None,
) -> list[SupportResult]:
    """Compute a `SupportResult` per object in `scene.objects` (same order).

    Assumes the scene is already valid (non-colliding, contained) -- this
    function does not re-check that.

    `epsilon` (meters) is the contact tolerance, used both for "is this object
    resting on that one" (gap between an object's lowest Y and the supporting
    floor/object's highest Y) and for which corners count as touching a contact
    plane. Deliberately larger (1e-3 = 1mm) than collision.py/containment.py's
    1e-6 float-noise epsilon -- this one has to absorb real
    reconstruction/measurement noise between two independently-placed objects'
    faces, not just float64 rounding.

    `geom` is the shared `SceneGeometry`; pass the one you already built
    (validator.py does) and it is reused as-is, otherwise it is precomputed
    here.
    """
    if geom is None:
        geom = precompute(scene)
    n = geom.n
    floor_hull = _xz_hull(geom.container_vertices[np.argsort(geom.container_vertices[:, 1])[:4]])

    # Contact masks for every object in two vectorized comparisons, packed to a
    # per-object corner bitmask so the face fast path is a dict lookup.
    verts_y = geom.vertices[:, :, 1]
    low_bits = ((verts_y <= geom.aabb_min[:, None, 1] + epsilon) @ _BIT_WEIGHTS).tolist()
    high_bits = ((verts_y >= geom.aabb_max[:, None, 1] - epsilon) @ _BIT_WEIGHTS).tolist()
    xz_all = [[tuple(p) for p in obj] for obj in geom.vertices[:, :, ::2].tolist()]

    supporters: list[list[int]] = [[] for _ in range(n)]
    supporting: set[int] = set()  # objects something actually rests on
    for top, bottom, _area in resting_pairs(geom, epsilon):
        supporters[top].append(bottom)
        supporting.add(bottom)

    bottom_pts: list[list[Pt]] = []
    bottom_hulls: list[list[Pt]] = []
    # Top footprints are only ever read for objects that support something, so
    # the rest stay None (in a flat layout that is every object).
    top_hulls: list[list[Pt] | None] = [None] * n
    for i, xz in enumerate(xz_all):
        lo_bits = low_bits[i]
        pts = [xz[k] for k in _BIT_INDICES[lo_bits]]
        bottom_pts.append(pts)
        hull = _face_hull(xz, _FACE_CYCLES.get(lo_bits))
        bottom_hulls.append(_hull(pts) if hull is None else hull)
        if i in supporting:
            hi_bits = high_bits[i]
            top = _face_hull(xz, _FACE_CYCLES.get(hi_bits))
            top_hulls[i] = (
                _hull([xz[k] for k in _BIT_INDICES[hi_bits]]) if top is None else top
            )

    floored = on_floor(geom, epsilon).tolist()
    centers_xz = [tuple(c) for c in geom.centers[:, ::2].tolist()]
    # Objects whose whole XZ footprint is inside a rectangular floor: their
    # bottom footprint survives the floor clip untouched (see `_axis_rect`).
    floor_rect = _axis_rect(floor_hull)
    if floor_rect is None:
        inside_floor = [False] * n
    else:
        fx0, fx1, fz0, fz1 = floor_rect
        lo, hi = geom.aabb_min, geom.aabb_max
        inside_floor = (
            (lo[:, 0] >= fx0) & (hi[:, 0] <= fx1) & (lo[:, 2] >= fz0) & (hi[:, 2] <= fz1)
        ).tolist()

    results: list[SupportResult] = []
    for i in range(n):
        bottom_hull = bottom_hulls[i]
        bottom_area = _poly_area(bottom_hull)
        degenerate = bottom_area < DEGENERATE_AREA_M2

        # floor first, then scene order; the flag is "the clip cannot cut this"
        candidates: list[tuple[str, list[Pt], bool]] = (
            [("container_floor", floor_hull, inside_floor[i])] if floored[i] else []
        )
        candidates += [(geom.ids[j], top_hulls[j], False) for j in sorted(supporters[i])]

        com = centers_xz[i]
        names: list[str] = []
        patch_areas: dict[str, float] = {}
        patch_vertices: list[Pt] = []
        covered = 0.0
        com_over_patch = False
        point_supported = [False] * len(bottom_pts[i]) if degenerate else []
        for name, top_hull, uncut in candidates:
            patch = bottom_hull if uncut else _clip(bottom_hull, top_hull)
            if not patch:
                continue
            if not com_over_patch and _signed_dist(com, patch) >= -TOUCH_TOL_M:
                com_over_patch = True
            names.append(name)
            # An uncut patch IS the bottom footprint, area included.
            area = bottom_area if patch is bottom_hull else _poly_area(patch)
            patch_areas[name] = area
            patch_vertices.extend(patch)
            covered += area
            if degenerate:
                for k, p in enumerate(bottom_pts[i]):
                    if not point_supported[k] and _signed_dist(p, top_hull) >= -TOUCH_TOL_M:
                        point_supported[k] = True

        if degenerate:
            support_ratio = (
                sum(point_supported) / len(point_supported) if point_supported else 0.0
            )
        else:
            support_ratio = min(1.0, covered / bottom_area)

        if len(names) == 1 and patch_vertices == bottom_hull:
            # Uncut single patch: the bottom footprint is already a canonical
            # hull (CCW, no collinear points, starts at its lex-min vertex), so
            # re-hulling it would return it unchanged.
            support_polygon = bottom_hull
        else:
            support_polygon = _hull(patch_vertices)
        if support_polygon:
            margin = _signed_dist(com, support_polygon)
            if -TOUCH_TOL_M < margin < 0.0:
                margin = 0.0  # COM on the boundary counts as supported
        else:
            margin = FLOATING_MARGIN_SENTINEL_M

        results.append(
            SupportResult(
                object_id=geom.ids[i],
                support_ratio=support_ratio,
                stability_margin_m=margin,
                supporting_objects=names,
                floating=support_ratio < floating_threshold,
                unstable=margin < 0,
                contact_polygon=[list(p) for p in support_polygon],
                patch_areas_m2=patch_areas,
                com_over_patch=com_over_patch,
            )
        )

    # Chain instability, bottom-up: a supporter's bottom is always below its
    # supportee's, so ascending aabb_min.y visits supporters first.
    by_id = {r.object_id: r for r in results}
    for i in np.argsort(geom.aabb_min[:, 1], kind="stable"):
        r = results[i]
        r.supported_by_unstable = any(
            by_id[s].floating or by_id[s].unstable or by_id[s].supported_by_unstable
            for s in r.supporting_objects
            if s != "container_floor"
        )
    return results
