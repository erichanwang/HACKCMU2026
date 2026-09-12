"""Scene -> PAN observation adapter (PAN.md section 7).

Mode B renders a deterministic RGB frame from the reconstructed scene (a tiny
software rasterizer -- pinhole projection + a numpy z-buffer, no OpenGL/cv2
needed). Mode A wraps a real iPhone RGB frame into the same `Observation`
shape. `build_pan_input` packs either into a `SimulationRequest` without
inventing action text (that is `pan.actions`' job, not ours).

Camera convention (see report for full detail): world is X=right/Y=up/Z=forward.
Both default viewpoints sit on the +Z ("front") side of the container looking
down toward -Z ("rear"), so in the rendered image +Z maps toward the BOTTOM
of the frame and -Z toward the top; +X maps to the right of the frame. That
convention is identical for container framing and scene framing -- only the
camera distance/centre change -- and `project_points` is the one place the
pinhole math lives, so anything that needs world -> pixel (the mock world
model included) agrees with the renderer exactly.

Framing: a realistic "current state" has unpacked items lying on the table
BESIDE the suitcase, so `observation_from_scene` frames the whole scene by
default (`frame="scene"`, `viewpoint_for_scene`) -- an object off to the side
is in shot, on a drawn table surface. `frame="container"` gives the old
container-only framing (`viewpoint_for_container`), which crops table items out.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Union

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from pan.types import Observation, PackingAction, SimulationRequest, Viewpoint
from physics.geometry import obb_from, obb_vertices
from physics.schema import Container, Object, Scene

# --------------------------------------------------------------------- palette
# sha256(id)[0] % 12 -> stable, deterministic color per object id. 12 was
# picked because it keeps all ids in tests/fixtures.py's valid_packed_scene()
# collision-free (verified by hand); if a future id set collides, widen the
# palette rather than special-casing ids.
# pan/evaluate.py segments frames back into objects by hue direction, so a face's shade
# (1.0 top, 0.9 front, 0.82 side, the only three the fixed light can show) never moves a
# pixel onto another entry. Measured minimum separation in that space across all 12
# entries and shades: 64.9 (index 5 vs 7) against color_tol = 60, an 8% margin. A 13th
# colour, a near-grey (saturation < ~0.5), or a wider tolerance can silently merge two
# objects again; re-measure before touching either.
_PALETTE: tuple[tuple[int, int, int], ...] = (
    (230, 25, 75), (60, 180, 75), (255, 195, 0), (0, 130, 200),
    (245, 130, 48), (145, 30, 180), (70, 240, 240), (240, 50, 230),
    (170, 200, 40), (0, 128, 128), (220, 90, 130), (140, 100, 60),
)


@lru_cache(maxsize=4096)
def color_for_id(object_id: str) -> tuple[int, int, int]:
    idx = hashlib.sha256(object_id.encode("utf-8")).digest()[0] % len(_PALETTE)
    return _PALETTE[idx]


# ----------------------------------------------------------------- rasterizer
# Box faces as obb_vertices index rings + local outward normals. Vertex order
# matches physics.geometry.obb_vertices' sign ordering [sx for sy for sz] ->
# index 0..7 = (---,--+,-+-,-++,+--,+-+,++-,+++). Each quad is listed as a
# closed ring (consecutive entries share an edge), which the edge-function
# inside test in `_raster_faces` relies on.
_FACE_IDX = np.array([(0, 1, 3, 2), (4, 5, 7, 6), (0, 1, 5, 4), (2, 3, 7, 6), (0, 2, 6, 4), (1, 3, 7, 5)])
_FACE_NORMALS = np.array([(-1.0, 0, 0), (1.0, 0, 0), (0, -1.0, 0), (0, 1.0, 0), (0, 0, -1.0), (0, 0, 1.0)])
_EDGES: tuple[tuple[int, int], ...] = (
    (0, 1), (2, 3), (4, 5), (6, 7),
    (0, 2), (1, 3), (4, 6), (5, 7),
    (0, 4), (1, 5), (2, 6), (3, 7),
)
# Light straight above-front, no X component: both default cameras sit at the
# container's centre X, so a box only ever shows ONE of its X sides and the two
# sharing a shade costs nothing visually. Levels are deliberately mild (top
# 1.0, front 0.9, side 0.82): `pan.evaluate.segment_by_color` assigns pixels to
# the nearest palette colour within a 60-RGB radius, and darker shades of one
# palette colour drift into another's radius (olive x0.7 reads as brown,
# pink x0.7 as brown). The dark 1 px edges carry the rest of the depth cue.
_LIGHT_DIR = np.array([0.0, 1.0, 0.35])
_LIGHT_DIR = _LIGHT_DIR / np.linalg.norm(_LIGHT_DIR)

_BG = (245, 245, 245)
_TABLE = (228, 224, 216)  # far from every palette color, so it never segments as an object
_GRID = (209, 206, 199)
_CONTAINER = (120, 160, 200)
_EDGE = np.array((34, 34, 34), dtype=np.uint8)
_LABEL = (22, 22, 22)
_HALO = (255, 255, 255)
_FONT = ImageFont.load_default(11)  # loaded once; per-call font lookups were a measurable cost
_GRID_DIVISIONS = 6


def _normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def _cross(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    # np.cross costs ~0.14 ms a call (axis shuffling); this is the same arithmetic
    return np.array([a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]])


def _shade(world_normal: np.ndarray) -> np.ndarray:
    """Flat per-face shading factor(s) from one fixed light, for any (..., 3)
    array of normals. The light-facing (top) face keeps the exact palette
    color (factor 1.0), fronts land at 0.9 and sides at 0.82 (see _LIGHT_DIR
    for why not darker)."""
    n = np.asarray(world_normal, dtype=float)
    n = n / np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-12)
    return np.clip(0.82 + 0.24 * (n @ _LIGHT_DIR), 0.6, 1.0)


def _view_direction(name: str) -> np.ndarray:
    """Unit vector from the look-at point toward the camera, per named geometry."""
    if name == "overhead_45":
        # above-front, looking down at ~45 degrees toward the center
        return _normalize(np.array([0.0, 1.0, 1.0]))
    if name == "front_high":
        # elevated but mostly front-on, so the open top is still visible
        return _normalize(np.array([0.0, 0.5, 1.0]))
    raise ValueError(f"unknown viewpoint name: {name!r}")


def _fit_viewpoint(
    name: str, lo: np.ndarray, hi: np.ndarray, *, width: int, height: int, fov_deg: float, margin: float
) -> Viewpoint:
    """Camera on the named direction from the AABB's centre, at the tightest
    distance that keeps all 8 corners in frame at this aspect ratio, backed off
    by the fractional `margin` -- so the box fills the canvas without clipping."""
    center = (lo + hi) / 2.0
    direction = _view_direction(name)
    forward = -direction
    right = _normalize(_cross(forward, np.array([0.0, 1.0, 0.0])))
    up = _cross(right, forward)
    tan_v = math.tan(math.radians(fov_deg / 2.0))
    tan_h = tan_v * (width / height)

    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    rel = corners - center
    # camera at center + direction*D  =>  depth(p) = D - rel.direction, and p is
    # in frame iff |rel.right| <= tan_h*depth and |rel.up| <= tan_v*depth.
    need = np.maximum(np.abs(rel @ right) / tan_h, np.abs(rel @ up) / tan_v) + rel @ direction
    distance = max(float(need.max()), 1e-3) * (1.0 + margin)

    cam_pos = center + direction * distance
    return Viewpoint(
        name=name,
        camera_position=tuple(float(v) for v in cam_pos),
        look_at=tuple(float(v) for v in center),
        up=(0.0, 1.0, 0.0),
        fov_deg=fov_deg,
        width=width,
        height=height,
    )


def viewpoint_for_container(
    container: Container,
    name: str,
    *,
    width: int = 512,
    height: int = 512,
    fov_deg: float = 60.0,
) -> Viewpoint:
    """Deterministic camera framing the whole container with margin, computed
    from its own dimensions/position so it works for any container size.

    ponytail: assumes an axis-aligned container (identity rotation), which is
    true of every fixture; add rotation support if a tilted container shows up.
    """
    pos = np.asarray(container.position, dtype=float)
    half = np.asarray(container.dimensions, dtype=float) / 2.0
    # a looser margin than scene framing: items packed above the rim still fit
    return _fit_viewpoint(name, pos - half, pos + half, width=width, height=height, fov_deg=fov_deg, margin=0.25)


def _scene_bounds(scene: Scene) -> tuple[np.ndarray, np.ndarray]:
    """World AABB (lo, hi) enclosing the container AND every object, rotations
    included (objects still on the table are part of it -- that is the point)."""
    verts = [obb_vertices(obb_from(scene.container))]
    verts += [obb_vertices(obb_from(o)) for o in scene.objects]
    allv = np.concatenate(verts)
    return allv.min(axis=0), allv.max(axis=0)


def _ground_rect(
    scene: Scene, pad: float = 0.04, bounds: Optional[tuple[np.ndarray, np.ndarray]] = None
) -> tuple[float, float, float, float, float]:
    """(x0, x1, z0, z1, floor_y) of the table surface: the scene's XZ bounds
    padded a little, at the container's floor height. Objects beside the
    suitcase therefore sit ON something instead of floating over the void.
    `bounds` lets a caller that already has `_scene_bounds` skip recomputing it."""
    lo, hi = bounds if bounds is not None else _scene_bounds(scene)
    cy = scene.container.position[1]
    hy = scene.container.dimensions[1] / 2.0
    return float(lo[0] - pad), float(hi[0] + pad), float(lo[2] - pad), float(hi[2] + pad), float(cy - hy)


def viewpoint_for_scene(
    scene: Scene,
    name: str,
    *,
    width: int = 512,
    height: int = 512,
    fov_deg: float = 60.0,
    margin: float = 0.1,
) -> Viewpoint:
    """Deterministic camera framing the union AABB of the container and every
    object (plus the drawn table surface), so unpacked items lying beside the
    suitcase are IN SHOT -- unlike `viewpoint_for_container`, which crops them.

    Same named geometries and same camera convention as `viewpoint_for_container`
    (+X right, +Z toward the image bottom); only the distance and look-at point
    differ. `margin` is the fractional standoff added to the tightest distance
    that still fits all 8 union-AABB corners in frame, so nothing is clipped at
    any aspect ratio.
    """
    lo, hi = _scene_bounds(scene)
    x0, x1, z0, z1, floor_y = _ground_rect(scene)
    lo = np.minimum(lo, [x0, floor_y, z0])
    hi = np.maximum(hi, [x1, floor_y, z1])
    return _fit_viewpoint(name, lo, hi, width=width, height=height, fov_deg=fov_deg, margin=margin)


_DEFAULT_CONTAINER = Container(id="carry_on", dimensions=(0.56, 0.23, 0.36), position=(0.0, 0.115, 0.0))
DEFAULT_VIEWPOINTS: dict[str, Viewpoint] = {
    "overhead_45": viewpoint_for_container(_DEFAULT_CONTAINER, "overhead_45"),
    "front_high": viewpoint_for_container(_DEFAULT_CONTAINER, "front_high"),
}


def _camera_basis(vp: Viewpoint) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    cam = np.array(vp.camera_position, dtype=float)
    forward = _normalize(np.array(vp.look_at, dtype=float) - cam)
    right = _normalize(_cross(forward, np.array(vp.up, dtype=float)))
    true_up = _cross(right, forward)
    return cam, right, true_up, forward


def _focal(vp: Viewpoint) -> float:
    return (vp.height / 2.0) / math.tan(math.radians(vp.fov_deg / 2.0))


def _dot3(points: np.ndarray, v: np.ndarray) -> np.ndarray:
    # explicit so a point projects to the same bits whether it is batched or alone
    return points[:, 0] * v[0] + points[:, 1] * v[1] + points[:, 2] * v[2]


def _camera_coords(vp: Viewpoint, points: np.ndarray) -> np.ndarray:
    """(N, 3) world -> (N, 3) camera space: x along right, y along up, z = depth."""
    cam, right, up, forward = _camera_basis(vp)
    rel = np.asarray(points, dtype=float).reshape(-1, 3) - cam
    return np.stack([_dot3(rel, right), _dot3(rel, up), _dot3(rel, forward)], axis=-1)


def _project_cam(vp: Viewpoint, xyz: np.ndarray) -> np.ndarray:
    z = xyz[:, 2]
    z_safe = np.where(z <= 1e-6, 1e-6, z)
    focal = _focal(vp)
    u = vp.width / 2.0 + focal * (xyz[:, 0] / z_safe)
    v = vp.height / 2.0 - focal * (xyz[:, 1] / z_safe)
    return np.stack([u, v], axis=-1)


def _project_points(vp: Viewpoint, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """points: (N, 3) world coords -> (uv (N, 2) pixel coords, depth (N,))."""
    xyz = _camera_coords(vp, points)
    return _project_cam(vp, xyz), xyz[:, 2]


def project_points(points_xyz, viewpoint: Union[Viewpoint, dict]) -> np.ndarray:
    """World points -> (N, 2) float pixel coords with the renderer's OWN pinhole
    math. THE single world->pixel entry point: the mock world model projects an
    action's target position with this so its synthesized motion lands where the
    renderer would have drawn the object.

    `viewpoint` may be a `Viewpoint` or the `asdict(viewpoint)` copy carried in
    `Observation.metadata["viewpoint"]`. Points behind the camera are clamped to
    a tiny positive depth (they project to absurd, far-off-screen pixels rather
    than raising).
    """
    vp = viewpoint if isinstance(viewpoint, Viewpoint) else Viewpoint(**viewpoint)
    pts = np.asarray(points_xyz, dtype=float).reshape(-1, 3)
    uv, _ = _project_points(vp, pts)
    return uv


def _find_object(scene: Scene, object_id: str) -> Optional[Object]:
    for o in scene.objects:
        if o.id == object_id:
            return o
    return None


def _pack(rgb: np.ndarray) -> np.ndarray:
    """(..., 3) uint8 -> (...) uint32 pixel values in the RGBA canvas's own
    memory layout, so a colour write is one scalar masked store on a 2-D view
    (a broadcast (h, w, 1) mask over 3 channels is ~5x slower in numpy)."""
    rgb = np.asarray(rgb, dtype=np.uint8)
    flat = rgb.reshape(-1, 3)
    rgba = np.concatenate([flat, np.full((len(flat), 1), 255, dtype=np.uint8)], axis=1)
    return np.frombuffer(rgba.tobytes(), dtype=np.uint32).reshape(rgb.shape[:-1])


_EDGE32 = _pack(_EDGE)[()]
_BG32 = _pack(np.array(_BG, dtype=np.uint8))[()]


def _raster_faces(
    img32: np.ndarray,
    zbuf: np.ndarray,
    vp: Viewpoint,
    quads: np.ndarray,
    n_cam: np.ndarray,
    d: np.ndarray,
    col32: np.ndarray,
    *,
    edge_only: bool = False,
) -> None:
    """Z-buffered fill of convex screen quads into `img32`, the (H, W) uint32
    view of the RGBA canvas, in place.

    Each face is rasterized over its own pixel bbox: PIL's C polygon fill gives
    the coverage mask (interior 1, 1 px outline 2) in one call, and every
    covered pixel is depth-tested with the face plane's 1/z -- affine in pixel
    space under perspective, so it is exact -- so any face order gives the
    right occlusion, including interpenetrating boxes. Outline pixels get the
    dark edge color, depth-tested too, so hidden edges stay hidden.
    `edge_only` draws just the outline without touching the z-buffer (the
    container's see-through front walls).

    quads: (k, 4, 2) pixel rings; n_cam: (k, 3) plane normals in camera space;
    d: (k,) plane offsets (n_cam . any vertex, camera space); col32: (k,) packed.
    """
    H, W = zbuf.shape
    focal = _focal(vp)
    # ray through pixel (u, v) is ((u - W/2)/f, -(v - H/2)/f, 1) * z, so on the
    # plane n.p = d:  1/z = (n . ray) / d  -- affine in (u, v).
    safe_d = np.where(np.abs(d) < 1e-12, 1.0, d)
    za = (n_cam[:, 0] / (focal * safe_d)).astype(np.float32)
    zb = (-n_cam[:, 1] / (focal * safe_d)).astype(np.float32)
    zc = ((n_cam[:, 2] - n_cam[:, 0] * (W / 2.0) / focal + n_cam[:, 1] * (H / 2.0) / focal) / safe_d).astype(np.float32)
    lo = np.maximum(np.floor(quads.min(1)), 0).astype(int)
    hi = np.minimum(np.ceil(quads.max(1)), [W - 1, H - 1]).astype(int)
    us = np.arange(W, dtype=np.float32)
    vs = np.arange(H, dtype=np.float32)[:, None]

    for f in range(len(quads)):
        (i0, j0), (i1, j1) = lo[f], hi[f]
        if i1 < i0 or j1 < j0 or abs(d[f]) < 1e-12:
            continue
        cover = Image.new("L", (i1 - i0 + 1, j1 - j0 + 1), 0)
        ImageDraw.Draw(cover).polygon((quads[f] - (i0, j0)).ravel().tolist(), fill=1, outline=2)
        mask = np.asarray(cover)
        invz = za[f] * us[i0:i1 + 1] + zb[f] * vs[j0:j1 + 1] + zc[f]
        zwin = zbuf[j0:j1 + 1, i0:i1 + 1]
        edge = mask == 2
        hit = (edge if edge_only else mask > 0) & (invz > zwin)
        if not hit.any():
            continue
        tile = img32[j0:j1 + 1, i0:i1 + 1]
        if edge_only:
            tile[...] = np.where(hit, _EDGE32, tile)
        else:
            np.maximum(zwin, invz * (mask > 0), out=zwin)  # == "zwin[hit] = invz[hit]", without the slow masked store
            tile[...] = np.where(hit, col32[f], tile)
            tile[...] = np.where(hit & edge, _EDGE32, tile)


@lru_cache(maxsize=512)
def _label_sprite(text: str) -> Image.Image:
    """`text` with a 1 px white halo, tightly cropped, as a pasteable RGBA sprite.

    Bilevel on purpose: an anti-aliased glyph edge over a coloured face blends
    into colours that `pan.evaluate.segment_by_color` can mistake for some other
    object, so every label pixel is exactly halo-white or text-dark.
    Cached: stroked FreeType text costs ~2 ms a label, a paste ~20 us, and a
    rollout draws the same object ids frame after frame."""
    left, top, right, bottom = _FONT.getbbox(text, stroke_width=1)
    size = (right - left, bottom - top)
    halo, ink = Image.new("1", size, 0), Image.new("1", size, 0)
    ImageDraw.Draw(halo).text((-left, -top), text, font=_FONT, fill=1, stroke_width=1, stroke_fill=1)
    ImageDraw.Draw(ink).text((-left, -top), text, font=_FONT, fill=1)
    sprite = Image.new("RGBA", size, (0, 0, 0, 0))
    sprite.paste(_HALO + (255,), mask=halo)
    sprite.paste(_LABEL + (255,), mask=ink)
    return sprite


def _draw_labels(im: Image.Image, labels: list[tuple[str, list[int]]]) -> None:
    """Greedy non-overlapping labels: centred above the object's pixel bbox,
    else below it, else skipped (an unreadable pile-up is worse than no label).
    Placed left to right so neighbours in a row alternate above/below."""
    width, height = im.size
    placed: list[tuple[int, int, int, int]] = []
    for text, (x0, y0, x1, y1) in sorted(labels, key=lambda t: (t[1][0], t[0])):
        sprite = _label_sprite(text)
        tw, th = sprite.size
        x = min(max((x0 + x1 - tw) // 2, 0), width - tw)
        for y in (y0 - th - 2, y1 + 2):
            y = min(max(y, 0), height - th)
            rect = (x - 2, y - 1, x + tw + 2, y + th + 1)  # padded, so neighbours don't abut
            if any(r[0] < rect[2] and rect[0] < r[2] and r[1] < rect[3] and rect[1] < r[3] for r in placed):
                continue
            im.paste(sprite, (x, y), sprite)
            placed.append(rect)
            break


def render_scene(
    scene: Scene,
    viewpoint: Viewpoint,
    *,
    highlight: Optional[str] = None,
    ghost: Optional[tuple[str, PackingAction]] = None,
) -> tuple[np.ndarray, dict]:
    """Software-rasterize `scene` from `viewpoint`. Deterministic: no randomness,
    no wall-clock, pure function of the inputs."""
    vp = viewpoint
    width, height = vp.width, vp.height
    objs = scene.objects
    n = len(objs)
    obbs = [obb_from(o) for o in objs]
    container_obb = obb_from(scene.container)

    # --- every world point -> camera -> pixel in ONE call: container + object
    # corners, object centres, then the table quad and its grid line endpoints.
    boxes = np.stack([obb_vertices(container_obb)] + [obb_vertices(b) for b in obbs])  # (n+1, 8, 3)
    centres = np.array([b.center for b in obbs], dtype=float).reshape(n, 3)
    allv = boxes.reshape(-1, 3)
    x0, x1, z0, z1, fy = _ground_rect(scene, bounds=(allv.min(axis=0), allv.max(axis=0)))
    gx = np.linspace(x0, x1, _GRID_DIVISIONS + 1)
    gz = np.linspace(z0, z1, _GRID_DIVISIONS + 1)
    ground = np.array(
        [(x0, fy, z0), (x1, fy, z0), (x1, fy, z1), (x0, fy, z1)]
        + [(x, fy, z) for x in gx for z in (z0, z1)]
        + [(x, fy, z) for z in gz for x in (x0, x1)]
    )
    cam_xyz = _camera_coords(vp, np.concatenate([boxes.reshape(-1, 3), centres, ground]))
    uv = _project_cam(vp, cam_xyz)
    k = 8 * (n + 1)
    box_cam = cam_xyz[:k].reshape(n + 1, 8, 3)
    box_uv = uv[:k].reshape(n + 1, 8, 2)
    ctr_uv = uv[k:k + n]
    g_uv = uv[k + n:]

    # --- box faces, all at once: (n+1, 6, ...) with the container at index 0
    _, right, up, forward = _camera_basis(vp)
    axes = np.stack([container_obb.axes] + [b.axes for b in obbs])
    n_world = np.einsum("bij,fj->bfi", axes, _FACE_NORMALS)  # outward normals (n+1, 6, 3)
    n_cam = n_world @ np.stack([right, up, forward]).T
    face_cam = box_cam[:, _FACE_IDX]  # (n+1, 6, 4, 3)
    face_uv = box_uv[:, _FACE_IDX]  # (n+1, 6, 4, 2)
    d = np.einsum("bfi,bfi->bf", n_cam, face_cam[:, :, 0])
    front = d < -1e-12  # outward normal points at the camera
    projectable = (face_cam[..., 2] > 1e-6).all(-1)  # every corner in front of the camera

    object_colors: dict[str, tuple[int, int, int]] = {o.id: color_for_id(o.id) for o in objs}
    base = np.array([_CONTAINER] + list(object_colors.values()), dtype=float)
    shade = _shade(n_world)
    shade[0] = _shade(-n_world[0])  # we see the container's INSIDE: shade by the inward normal
    face_rgb = np.rint(base[:, None, :] * shade[..., None]).astype(np.uint8)

    # --- table, grid, container floor + back walls (PIL, far to near). These
    # are the big faces, and everything else in shot is inside or beside the
    # container, i.e. nearer than them, so painting them first is exact and
    # skips a per-pixel depth test over ~100k pixels.
    # ponytail: an item on the table BEHIND the container would show through
    # its back wall; route the container through `_raster_faces` if that shows up.
    #
    # One RGBA canvas shared zero-copy between PIL and numpy (PIL can only
    # share 4-byte pixel modes; frombuffer marks it read-only, which ImageDraw
    # would silently copy-on-write, so clear the flag): no PIL<->numpy round
    # trips, which cost ~0.5 ms each at 512^2.
    canvas = np.empty((height, width, 4), dtype=np.uint8)
    img32 = canvas.view(np.uint32).reshape(height, width)
    img32[...] = _BG32
    im = Image.frombuffer("RGBA", (width, height), canvas, "raw", "RGBA", 0, 1)
    im.readonly = 0
    draw = ImageDraw.Draw(im)
    draw.polygon(g_uv[:4].ravel().tolist(), fill=_TABLE)
    for a, b in g_uv[4:].reshape(-1, 2, 2).tolist():
        draw.line([tuple(a), tuple(b)], fill=_GRID, width=1)
    back = np.nonzero(~front[0] & projectable[0])[0]
    for f in sorted(back, key=lambda f: -face_cam[0, f, :, 2].mean()):
        draw.polygon(face_uv[0, f].ravel().tolist(), fill=tuple(face_rgb[0, f].tolist()), outline=tuple(_EDGE.tolist()))

    # --- objects: z-buffered (front faces only; back faces are covered by them)
    face_col32 = _pack(face_rgb)
    zbuf = np.zeros((height, width), dtype=np.float32)  # stores 1/z; 0 = infinitely far
    bi, fi = np.nonzero(front & projectable)
    keep = bi > 0
    bi, fi = bi[keep], fi[keep]
    _raster_faces(img32, zbuf, vp, face_uv[bi, fi], n_cam[bi, fi], d[bi, fi], face_col32[bi, fi])
    fi = np.nonzero(front[0] & projectable[0])[0]  # see-through front walls + rim: depth-tested edges only
    _raster_faces(img32, zbuf, vp, face_uv[0, fi], n_cam[0, fi], d[0, fi], face_col32[0, fi], edge_only=True)

    # --- overlays (PIL, same canvas): highlight, ghost, labels
    obj_uv = box_uv[1:]
    lo = np.floor(obj_uv.min(1)).astype(int) if n else np.zeros((0, 2), int)
    hi = np.ceil(obj_uv.max(1)).astype(int) if n else np.zeros((0, 2), int)
    object_bboxes: dict[str, list[int]] = {}
    object_visible: dict[str, bool] = {}
    projected_centroids: dict[str, tuple[float, float]] = {}
    for i, obj in enumerate(objs):
        bbox = [int(lo[i, 0]), int(lo[i, 1]), int(hi[i, 0]), int(hi[i, 1])]
        object_bboxes[obj.id] = bbox
        object_visible[obj.id] = bool(bbox[0] < width and bbox[2] >= 0 and bbox[1] < height and bbox[3] >= 0)
        projected_centroids[obj.id] = (float(ctr_uv[i, 0]), float(ctr_uv[i, 1]))

    def _edges(obj: Object, color: tuple[int, ...], line_width: int) -> None:
        puv, _ = _project_points(vp, obb_vertices(obb_from(obj)))
        for a, b in _EDGES:
            draw.line([tuple(puv[a]), tuple(puv[b])], fill=color, width=line_width)

    if highlight is not None:
        obj = _find_object(scene, highlight)
        if obj is not None:
            _edges(obj, (255, 255, 255, 255), 3)

    if ghost is not None:
        ghost_id, ghost_action = ghost
        obj = _find_object(scene, ghost_id)
        if obj is not None:
            ghost_obj = replace(
                obj,
                position=tuple(ghost_action.target_position),
                rotation=tuple(ghost_action.target_rotation),
            )
            _edges(ghost_obj, object_colors.get(ghost_id, (255, 255, 255)) + (160,), 2)

    _draw_labels(im, [(o.id, object_bboxes[o.id]) for o in objs if object_visible[o.id]])

    image = np.frombuffer(im.tobytes("raw", "RGB"), dtype=np.uint8).reshape(height, width, 3).copy()
    meta = {
        "object_colors": object_colors,
        "viewpoint": asdict(vp),
        "projected_centroids": projected_centroids,
        # Pixel bbox of each object's 8 projected OBB vertices, and whether that
        # bbox intersects the image at all (an item on the table can be fully
        # off-screen under container framing). Cheap, and it saves the mock /
        # evaluator from re-deriving projected extents.
        "object_bboxes": object_bboxes,
        "object_visible": object_visible,
        "container_color": _CONTAINER,
    }
    return image, meta


def _resolve_viewpoint(
    scene: Scene, viewpoint: Union[str, Viewpoint], frame: str = "scene", **kwargs: Any
) -> Viewpoint:
    if isinstance(viewpoint, Viewpoint):
        return viewpoint
    if frame == "scene":
        return viewpoint_for_scene(scene, viewpoint, **kwargs)
    if frame == "container":
        return viewpoint_for_container(scene.container, viewpoint, **kwargs)
    raise ValueError(f"unknown frame={frame!r}, expected 'scene' or 'container'")


def observation_from_scene(
    scene: Scene,
    viewpoint_name: Union[str, Viewpoint] = "overhead_45",
    scene_id: Optional[str] = None,
    *,
    width: int = 512,
    height: int = 512,
    fov_deg: float = 60.0,
    frame: str = "scene",
    **render_kwargs: Any,
) -> Observation:
    """Mode B: render a deterministic RGB frame of `scene`. `timestamp` is fixed
    to 0.0 (rendered observations must be reproducible byte-for-byte).

    `frame="scene"` (default) frames the container AND every object, so items
    still on the table are visible to PAN and to the evaluator; `"container"`
    frames only the suitcase (the old behaviour), which crops them out.
    Ignored when `viewpoint_name` is an explicit `Viewpoint`.
    """
    viewpoint = _resolve_viewpoint(
        scene, viewpoint_name, frame, width=width, height=height, fov_deg=fov_deg
    )
    image, meta = render_scene(scene, viewpoint, **render_kwargs)
    return Observation(
        image=image,
        scene_id=scene_id if scene_id is not None else scene.container.id,
        source="rendered",
        viewpoint=viewpoint,
        timestamp=0.0,
        object_ids=[o.id for o in scene.objects],
        metadata=meta,
    )


# ----------------------------------------------------------------------- Mode A
def _letterbox_params(src_w: int, src_h: int, target_w: int, target_h: int) -> tuple[float, int, int, int, int]:
    scale = min(target_w / src_w, target_h / src_h)
    new_w, new_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
    off_x, off_y = (target_w - new_w) // 2, (target_h - new_h) // 2
    return scale, new_w, new_h, off_x, off_y


def canonicalize(image: Union[Image.Image, np.ndarray], size: tuple[int, int]) -> np.ndarray:
    """Letterbox-resize `image` to `size` (w, h), preserving aspect ratio with padding."""
    pil = Image.fromarray(image.astype(np.uint8), "RGB") if isinstance(image, np.ndarray) else image.convert("RGB")
    target_w, target_h = size
    src_w, src_h = pil.size
    scale, new_w, new_h, off_x, off_y = _letterbox_params(src_w, src_h, target_w, target_h)
    resized = pil.resize((new_w, new_h), Image.BILINEAR)
    canvas = Image.new("RGB", (target_w, target_h), (0, 0, 0))
    canvas.paste(resized, (off_x, off_y))
    return np.array(canvas, dtype=np.uint8)


def observation_from_image(
    path_or_array: Union[str, Path, np.ndarray, Image.Image],
    scene: Scene,
    *,
    scene_id: str,
    crop: Optional[tuple[int, int, int, int]] = None,
    size: tuple[int, int] = (512, 512),
    frame_index: Optional[int] = None,
    timestamp: Optional[float] = None,
) -> Observation:
    """Mode A: wrap a real iPhone RGB frame. Records crop/original_size/letterbox
    metadata so pixel coordinates in `image` can be mapped back to the source frame."""
    if isinstance(path_or_array, np.ndarray):
        pil = Image.fromarray(path_or_array.astype(np.uint8), "RGB")
    elif isinstance(path_or_array, Image.Image):
        pil = ImageOps.exif_transpose(path_or_array)
    else:
        pil = ImageOps.exif_transpose(Image.open(path_or_array))
    pil = pil.convert("RGB")
    original_size = pil.size  # (w, h)

    if crop is not None:
        pil = pil.crop(crop)

    src_w, src_h = pil.size
    target_w, target_h = size
    scale, new_w, new_h, off_x, off_y = _letterbox_params(src_w, src_h, target_w, target_h)
    image = canonicalize(pil, size)

    metadata: dict[str, Any] = {
        "crop": list(crop) if crop is not None else None,
        "original_size": list(original_size),
        "letterbox": {
            "scale": scale,
            "cropped_size": [src_w, src_h],
            "resized_size": [new_w, new_h],
            "offset": [off_x, off_y],
        },
    }
    if frame_index is not None:
        metadata["frame_index"] = frame_index

    kwargs: dict[str, Any] = {}
    if timestamp is not None:
        kwargs["timestamp"] = timestamp

    return Observation(
        image=image,
        scene_id=scene_id,
        source="ios_rgb",
        viewpoint=None,
        object_ids=[o.id for o in scene.objects],
        metadata=metadata,
        **kwargs,
    )


# ------------------------------------------------------------------------ common
def save_observation(obs: Observation, path: Union[str, Path]) -> Observation:
    """Write `obs.image` as a PNG and record `image_path` on the observation."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(obs.image.astype(np.uint8), "RGB").save(path)
    obs.image_path = str(path)
    return obs


def build_pan_input(
    scene: Scene,
    action: PackingAction,
    viewpoint: Union[str, Viewpoint] = "overhead_45",
    *,
    observation: Optional[Observation] = None,
    options: Optional[dict] = None,
    history: Optional[list] = None,
) -> SimulationRequest:
    """Pack a scene + already-described action into a `SimulationRequest`.
    Uses `observation` as-is when given (Mode A); otherwise renders one (Mode B).
    Never generates action text -- that belongs to the action-language module."""
    if observation is None:
        observation = observation_from_scene(scene, viewpoint)
    return SimulationRequest(
        observation=observation,
        action=action,
        history=history or [],
        options=options or {},
    )
