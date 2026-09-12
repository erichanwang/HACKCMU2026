"""Scene -> PAN observation adapter (PAN.md section 7).

Mode B renders a deterministic RGB frame from the reconstructed scene (a tiny
software rasterizer -- pinhole projection + painter's algorithm, no OpenGL/cv2
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
from pathlib import Path
from typing import Any, Optional, Union

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from pan.types import Observation, PackingAction, SimulationRequest, Viewpoint
from physics.geometry import OBB, obb_from, obb_vertices
from physics.schema import Container, Object, Scene

# --------------------------------------------------------------------- palette
# sha256(id)[0] % 12 -> stable, deterministic color per object id. 12 was
# picked because it keeps all ids in tests/fixtures.py's valid_packed_scene()
# collision-free (verified by hand); if a future id set collides, widen the
# palette rather than special-casing ids.
_PALETTE: tuple[tuple[int, int, int], ...] = (
    (230, 25, 75), (60, 180, 75), (255, 195, 0), (0, 130, 200),
    (245, 130, 48), (145, 30, 180), (70, 240, 240), (240, 50, 230),
    (170, 200, 40), (0, 128, 128), (220, 90, 130), (140, 100, 60),
)


def color_for_id(object_id: str) -> tuple[int, int, int]:
    idx = hashlib.sha256(object_id.encode("utf-8")).digest()[0] % len(_PALETTE)
    return _PALETTE[idx]


# ----------------------------------------------------------------- rasterizer
# Face definitions: (obb_vertices index order forming a planar quad, local
# outward normal). Vertex order matches physics.geometry.obb_vertices' sign
# ordering [sx in (-1,1) for sy in (-1,1) for sz in (-1,1)] -> index
# 0..7 = (---,--+,-+-,-++,+--,+-+,++-,+++).
_FACES: dict[str, tuple[tuple[int, int, int, int], tuple[float, float, float]]] = {
    "-x": ((0, 1, 3, 2), (-1.0, 0.0, 0.0)),
    "+x": ((4, 5, 7, 6), (1.0, 0.0, 0.0)),
    "-y": ((0, 1, 5, 4), (0.0, -1.0, 0.0)),
    "+y": ((2, 3, 7, 6), (0.0, 1.0, 0.0)),
    "-z": ((0, 2, 6, 4), (0.0, 0.0, -1.0)),
    "+z": ((1, 3, 7, 5), (0.0, 0.0, 1.0)),
}
_EDGES: tuple[tuple[int, int], ...] = (
    (0, 1), (2, 3), (4, 5), (6, 7),
    (0, 2), (1, 3), (4, 6), (5, 7),
    (0, 4), (1, 5), (2, 6), (3, 7),
)
_LIGHT_DIR = np.array([0.4, 1.0, 0.3])
_LIGHT_DIR = _LIGHT_DIR / np.linalg.norm(_LIGHT_DIR)


def _normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def _shade(world_normal: np.ndarray) -> float:
    """Flat per-face shading factor; clamped so the base hue stays identifiable
    (never fully dark) and the brightest (light-facing) face keeps the exact
    palette color."""
    factor = float(np.dot(_normalize(world_normal), _LIGHT_DIR))
    return max(0.35, min(1.0, 0.65 + 0.5 * factor))


def _view_direction(name: str) -> np.ndarray:
    """Unit vector from the look-at point toward the camera, per named geometry."""
    if name == "overhead_45":
        # above-front, looking down at ~45 degrees toward the center
        return _normalize(np.array([0.0, 1.0, 1.0]))
    if name == "front_high":
        # elevated but mostly front-on, so the open top is still visible
        return _normalize(np.array([0.0, 0.5, 1.0]))
    raise ValueError(f"unknown viewpoint name: {name!r}")


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
    cx, cy, cz = container.position
    hx, hy, hz = (d / 2.0 for d in container.dimensions)
    center = (cx, cy, cz)
    radius = math.sqrt(hx * hx + hy * hy + hz * hz)
    margin = 1.6
    distance = radius / math.tan(math.radians(fov_deg / 2.0)) * margin

    cam_pos = np.array(center) + _view_direction(name) * distance
    return Viewpoint(
        name=name,
        camera_position=tuple(float(v) for v in cam_pos),
        look_at=center,
        up=(0.0, 1.0, 0.0),
        fov_deg=fov_deg,
        width=width,
        height=height,
    )


def _scene_bounds(scene: Scene) -> tuple[np.ndarray, np.ndarray]:
    """World AABB (lo, hi) enclosing the container AND every object, rotations
    included (objects still on the table are part of it -- that is the point)."""
    verts = [obb_vertices(obb_from(scene.container))]
    verts += [obb_vertices(obb_from(o)) for o in scene.objects]
    allv = np.concatenate(verts)
    return allv.min(axis=0), allv.max(axis=0)


def _ground_rect(scene: Scene, pad: float = 0.04) -> tuple[float, float, float, float, float]:
    """(x0, x1, z0, z1, floor_y) of the table surface: the scene's XZ bounds
    padded a little, at the container's floor height. Objects beside the
    suitcase therefore sit ON something instead of floating over the void."""
    lo, hi = _scene_bounds(scene)
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
    margin: float = 0.15,
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
    center = (lo + hi) / 2.0

    direction = _view_direction(name)
    forward = -direction
    right = _normalize(np.cross(forward, np.array([0.0, 1.0, 0.0])))
    up = np.cross(right, forward)
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


_DEFAULT_CONTAINER = Container(id="carry_on", dimensions=(0.56, 0.23, 0.36), position=(0.0, 0.115, 0.0))
DEFAULT_VIEWPOINTS: dict[str, Viewpoint] = {
    "overhead_45": viewpoint_for_container(_DEFAULT_CONTAINER, "overhead_45"),
    "front_high": viewpoint_for_container(_DEFAULT_CONTAINER, "front_high"),
}


def _camera_basis(vp: Viewpoint) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    cam = np.array(vp.camera_position, dtype=float)
    forward = _normalize(np.array(vp.look_at, dtype=float) - cam)
    right = _normalize(np.cross(forward, np.array(vp.up, dtype=float)))
    true_up = np.cross(right, forward)
    return cam, right, true_up, forward


def _project_points(vp: Viewpoint, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """points: (N, 3) world coords -> (uv (N, 2) pixel coords, depth (N,))."""
    cam, right, up, forward = _camera_basis(vp)
    rel = points - cam
    x = rel @ right
    y = rel @ up
    z = rel @ forward
    focal = (vp.height / 2.0) / math.tan(math.radians(vp.fov_deg / 2.0))
    z_safe = np.where(z <= 1e-6, 1e-6, z)
    u = vp.width / 2.0 + focal * (x / z_safe)
    v = vp.height / 2.0 - focal * (y / z_safe)
    return np.stack([u, v], axis=-1), z


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


_TABLE_COLOR = (228, 224, 216, 255)  # table surface: far from every palette color, so it never segments as an object
_GRID_COLOR = (210, 210, 210, 255)


def _draw_ground(draw: ImageDraw.ImageDraw, vp: Viewpoint, scene: Scene, divisions: int = 6) -> None:
    """Fill + grid the whole framed ground area (container floor height), not
    just the container footprint, so items beside the suitcase rest on a table."""
    x0, x1, z0, z1, y = _ground_rect(scene)
    quad = project_points([(x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1)], vp)
    draw.polygon([tuple(p) for p in quad], fill=_TABLE_COLOR)
    for x in np.linspace(x0, x1, divisions + 1):
        uv = project_points([(x, y, z0), (x, y, z1)], vp)
        draw.line([tuple(uv[0]), tuple(uv[1])], fill=_GRID_COLOR, width=1)
    for z in np.linspace(z0, z1, divisions + 1):
        uv = project_points([(x0, y, z), (x1, y, z)], vp)
        draw.line([tuple(uv[0]), tuple(uv[1])], fill=_GRID_COLOR, width=1)


def _draw_container_faces(
    draw: ImageDraw.ImageDraw, vp: Viewpoint, obb: OBB, verts3d: np.ndarray, cam_pos: np.ndarray, *, front: bool
) -> None:
    uv, _ = _project_points(vp, verts3d)
    for idx, normal_local in _FACES.values():
        world_normal = _normalize(obb.axes @ np.array(normal_local))
        face_center = verts3d[list(idx)].mean(axis=0)
        facing_camera = np.dot(cam_pos - face_center, world_normal) > 0
        poly = [tuple(uv[i]) for i in idx]
        if front:
            if facing_camera:
                draw.polygon(poly, outline=(40, 40, 40, 255))
        else:
            if not facing_camera:
                draw.polygon(poly, fill=(120, 160, 200, 60))


def _draw_object_edges(draw: ImageDraw.ImageDraw, vp: Viewpoint, obb: OBB, color: tuple[int, int, int, int], width: int = 2) -> None:
    uv, _ = _project_points(vp, obb_vertices(obb))
    for a, b in _EDGES:
        draw.line([tuple(uv[a]), tuple(uv[b])], fill=color, width=width)


def render_scene(
    scene: Scene,
    viewpoint: Viewpoint,
    *,
    highlight: Optional[str] = None,
    ghost: Optional[tuple[str, PackingAction]] = None,
) -> tuple[np.ndarray, dict]:
    """Software-rasterize `scene` from `viewpoint`. Deterministic: no randomness,
    no wall-clock, pure function of the inputs."""
    width, height = viewpoint.width, viewpoint.height
    im = Image.new("RGBA", (width, height), (245, 245, 245, 255))
    draw = ImageDraw.Draw(im, "RGBA")

    container_obb = obb_from(scene.container)
    container_verts = obb_vertices(container_obb)
    cam_pos = np.array(viewpoint.camera_position, dtype=float)

    _draw_ground(draw, viewpoint, scene)
    _draw_container_faces(draw, viewpoint, container_obb, container_verts, cam_pos, front=False)

    object_colors: dict[str, tuple[int, int, int]] = {o.id: color_for_id(o.id) for o in scene.objects}
    object_bboxes: dict[str, list[int]] = {}
    object_visible: dict[str, bool] = {}

    face_records: list[tuple[float, float, np.ndarray, tuple[int, int, int, int]]] = []
    for obj in scene.objects:
        obb = obb_from(obj)
        verts3d = obb_vertices(obb)
        uv, depth = _project_points(viewpoint, verts3d)
        base_color = object_colors[obj.id]
        bbox = [
            int(math.floor(uv[:, 0].min())), int(math.floor(uv[:, 1].min())),
            int(math.ceil(uv[:, 0].max())), int(math.ceil(uv[:, 1].max())),
        ]
        object_bboxes[obj.id] = bbox
        object_visible[obj.id] = bool(bbox[0] < width and bbox[2] >= 0 and bbox[1] < height and bbox[3] >= 0)
        # secondary sort key: the object's own centroid depth. Two faces from
        # different (stacked) objects can land at the exact same face-centroid
        # depth (a flush stack can make this an exact tie, not just fp noise);
        # breaking ties by whole-object depth keeps the physically-nearer
        # object on top instead of leaving it to insertion order. Both keys are
        # rounded to 9 decimals (nanometer-scale on this meter-scale geometry)
        # so ~1e-16 float noise from summation order doesn't masquerade as a
        # real depth difference and defeat the tie-break.
        obj_depth = round(float(_project_points(viewpoint, obb.center.reshape(1, 3))[1][0]), 9)
        for idx, normal_local in _FACES.values():
            world_normal = obb.axes @ np.array(normal_local)
            shade = _shade(world_normal)
            face_color = tuple(int(round(c * shade)) for c in base_color) + (255,)
            quad_uv = uv[list(idx)]
            quad_depth = round(float(depth[list(idx)].mean()), 9)
            face_records.append((quad_depth, obj_depth, quad_uv, face_color))

    face_records.sort(key=lambda r: (r[0], r[1]), reverse=True)  # far to near (painter's algorithm)
    for _, _, quad_uv, face_color in face_records:
        draw.polygon([tuple(p) for p in quad_uv], fill=face_color, outline=(20, 20, 20, 255))

    _draw_container_faces(draw, viewpoint, container_obb, container_verts, cam_pos, front=True)

    if highlight is not None:
        obj = _find_object(scene, highlight)
        if obj is not None:
            _draw_object_edges(draw, viewpoint, obb_from(obj), color=(255, 255, 255, 255), width=3)

    if ghost is not None:
        ghost_id, ghost_action = ghost
        obj = _find_object(scene, ghost_id)
        if obj is not None:
            ghost_obj = replace(
                obj,
                position=tuple(ghost_action.target_position),
                rotation=tuple(ghost_action.target_rotation),
            )
            ghost_rgb = object_colors.get(ghost_id, (255, 255, 255))
            _draw_object_edges(draw, viewpoint, obb_from(ghost_obj), color=ghost_rgb + (160,), width=2)

    projected_centroids: dict[str, tuple[float, float]] = {}
    for obj in scene.objects:
        obb = obb_from(obj)
        uv, _ = _project_points(viewpoint, obb.center.reshape(1, 3))
        cu, cv = float(uv[0, 0]), float(uv[0, 1])
        projected_centroids[obj.id] = (cu, cv)
        draw.text((cu + 3, cv + 3), obj.id, fill=(15, 15, 15, 255))

    image = np.array(im.convert("RGB"), dtype=np.uint8)
    meta = {
        "object_colors": object_colors,
        "viewpoint": asdict(viewpoint),
        "projected_centroids": projected_centroids,
        # Pixel bbox of each object's 8 projected OBB vertices, and whether that
        # bbox intersects the image at all (an item on the table can be fully
        # off-screen under container framing). Cheap, and it saves the mock /
        # evaluator from re-deriving projected extents.
        "object_bboxes": object_bboxes,
        "object_visible": object_visible,
        "container_color": (120, 160, 200),
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
