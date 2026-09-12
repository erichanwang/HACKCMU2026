"""Per-scene precomputed geometry, built ONCE and shared by every check.

Why this exists: profiling `validate_layout` showed >50% of time spent
rebuilding the same OBB vertices pair-by-pair in the broad phase, and each
sibling module (containment/support/constraints) re-running `obb_from` on
every object. Everything downstream takes a `SceneGeometry` and indexes into
its arrays instead of recomputing.

Conventions (same as physics.schema): meters, X=right / Y=up / Z=forward,
quaternion (x, y, z, w). Object order everywhere is `scene.objects` order;
`index[id]` maps an id to that row.

Malformed input: `precompute` raises `MalformedSceneError` (a `ValueError`
carrying `.object_id`) for duplicate ids, non-finite / non-positive
dimensions, non-finite positions, or zero / non-finite quaternions. The fast
path validates all objects with a few array ops; only when something is wrong
does it fall back to `physics.geometry.obb_from` per object, so the error
message is the single canonical one from `geometry.py`.

Complexity: `precompute` is O(n) with a constant number of numpy calls
(batched quaternion->matrix, one einsum for all 8n vertices).
`aabb_candidate_pairs` is O(n^2) but one broadcast, so ~1600 comparisons for
n=40 cost microseconds, not Python-loop milliseconds.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from physics.geometry import OBB, SIGNS, obb_from, obb_vertices
from physics.schema import Object, Scene


class MalformedSceneError(ValueError):
    """ValueError that names the offending object (or container) id."""

    def __init__(self, object_id: str | None, message: str):
        super().__init__(message)
        self.object_id = object_id


def check_no_duplicate_ids(scene: Scene) -> None:
    """Raise MalformedSceneError if any object id repeats or equals the container id."""
    seen: set[str] = {scene.container.id}
    for obj in scene.objects:
        if obj.id in seen:
            raise MalformedSceneError(obj.id, f"duplicate object id: {obj.id!r}")
        seen.add(obj.id)


@dataclass
class SceneGeometry:
    scene: Scene
    objects: list[Object]
    ids: list[str]
    index: dict[str, int]
    container_obb: OBB
    container_vertices: np.ndarray  # (8, 3)
    container_floor_y: float  # min world-Y of the container OBB
    obbs: list[OBB]
    centers: np.ndarray  # (n, 3)
    axes: np.ndarray  # (n, 3, 3), axes[n][:, k] is object n's local axis k in world
    half_extents: np.ndarray  # (n, 3)
    vertices: np.ndarray  # (n, 8, 3) world-space corners, SIGNS order
    aabb_min: np.ndarray  # (n, 3)
    aabb_max: np.ndarray  # (n, 3)
    masses: np.ndarray  # (n,)

    @property
    def n(self) -> int:
        return len(self.objects)


def _quats_to_matrices(q: np.ndarray) -> np.ndarray:
    """(n, 4) (x, y, z, w) -> (n, 3, 3). Same formula and same re-normalization
    rule as `physics.geometry.quat_to_matrix` (norm off by > 1e-3 -> normalize).
    Caller guarantees finite, non-zero norms."""
    n2 = np.einsum("ni,ni->n", q, q)
    scale = np.where(np.abs(n2 - 1.0) > 1e-3, n2**-0.5, 1.0)
    x, y, z, w = (q * scale[:, None]).T
    m = np.empty((q.shape[0], 3, 3))
    m[:, 0, 0] = 1 - 2 * (y * y + z * z)
    m[:, 0, 1] = 2 * (x * y - z * w)
    m[:, 0, 2] = 2 * (x * z + y * w)
    m[:, 1, 0] = 2 * (x * y + z * w)
    m[:, 1, 1] = 1 - 2 * (x * x + z * z)
    m[:, 1, 2] = 2 * (y * z - x * w)
    m[:, 2, 0] = 2 * (x * z - y * w)
    m[:, 2, 1] = 2 * (y * z + x * w)
    m[:, 2, 2] = 1 - 2 * (x * x + y * y)
    return m


def _slow_path_raise(objects: list[Object]) -> None:
    """Find the first malformed object with the canonical per-object check and
    raise a MalformedSceneError naming it."""
    for o in objects:
        try:
            obb_from(o)
        except ValueError as e:
            raise MalformedSceneError(o.id, str(e)) from e


def precompute(scene: Scene) -> SceneGeometry:
    """Build all shared arrays. Raises MalformedSceneError on bad input."""
    check_no_duplicate_ids(scene)
    try:
        container_obb = obb_from(scene.container)
    except ValueError as e:
        raise MalformedSceneError(scene.container.id, str(e)) from e
    container_vertices = obb_vertices(container_obb)
    floor_y = float(container_vertices[:, 1].min())
    objects = list(scene.objects)
    n = len(objects)
    if n == 0:
        empty3 = np.zeros((0, 3))
        return SceneGeometry(
            scene, objects, [], {}, container_obb, container_vertices, floor_y, [],
            empty3, np.zeros((0, 3, 3)), empty3, np.zeros((0, 8, 3)), empty3, empty3, np.zeros(0),
        )

    try:
        dims = np.array([o.dimensions for o in objects], dtype=float).reshape(n, 3)
        centers = np.array([o.position for o in objects], dtype=float).reshape(n, 3)
        quats = np.array([o.rotation for o in objects], dtype=float).reshape(n, 4)
    except (ValueError, TypeError):
        _slow_path_raise(objects)
        raise  # pragma: no cover - slow path always raises for malformed input
    qn = np.einsum("ni,ni->n", quats, quats)
    ok = (
        np.isfinite(dims).all(axis=1) & (dims > 0).all(axis=1)
        & np.isfinite(centers).all(axis=1)
        & np.isfinite(qn) & (qn >= 1e-12)
    )
    if not ok.all():
        _slow_path_raise(objects)
        bad = int(np.nonzero(~ok)[0][0])  # pragma: no cover - defensive
        raise MalformedSceneError(objects[bad].id, f"{objects[bad].id}: invalid geometry")

    axes = _quats_to_matrices(quats)
    half_extents = dims / 2.0
    # vertices[n, v, j] = centers[n, j] + sum_k SIGNS[v, k] * he[n, k] * axes[n, j, k]
    vertices = centers[:, None, :] + np.einsum("vk,nk,njk->nvj", SIGNS, half_extents, axes)
    ids = [o.id for o in objects]
    return SceneGeometry(
        scene=scene,
        objects=objects,
        ids=ids,
        index={oid: i for i, oid in enumerate(ids)},
        container_obb=container_obb,
        container_vertices=container_vertices,
        container_floor_y=floor_y,
        obbs=[OBB(center=centers[i], axes=axes[i], half_extents=half_extents[i], id=ids[i]) for i in range(n)],
        centers=centers,
        axes=axes,
        half_extents=half_extents,
        vertices=vertices,
        aabb_min=vertices.min(axis=1),
        aabb_max=vertices.max(axis=1),
        masses=np.array([o.mass_kg for o in objects], dtype=float),
    )


def aabb_candidate_pairs(geom: SceneGeometry, epsilon: float = 0.0) -> np.ndarray:
    """All (i, j), i < j, whose world AABBs overlap (padded by `epsilon`).
    Returns an (m, 2) int array. Vectorized O(n^2) broadcast -- the cheap
    necessary condition before any SAT narrow phase."""
    n = geom.n
    if n < 2:
        return np.zeros((0, 2), dtype=int)
    lo, hi = geom.aabb_min, geom.aabb_max
    overlap = np.all((hi[:, None, :] + epsilon >= lo[None, :, :])
                     & (hi[None, :, :] + epsilon >= lo[:, None, :]), axis=2)
    iu = np.triu_indices(n, k=1)
    mask = overlap[iu]
    return np.stack([iu[0][mask], iu[1][mask]], axis=1)


def xz_overlap_area(geom: SceneGeometry, i: int, j: int) -> float:
    """Overlap area of objects i and j's XZ axis-aligned footprints (from
    their AABBs). 0.0 when disjoint. Exact for yaw-only rotation, conservative
    (over-estimate) otherwise. Modules needing exact rotated footprints clip
    polygons themselves; this is the shared *topology* test (touching or not)."""
    lo_i, hi_i, lo_j, hi_j = geom.aabb_min[i], geom.aabb_max[i], geom.aabb_min[j], geom.aabb_max[j]
    dx = min(hi_i[0], hi_j[0]) - max(lo_i[0], lo_j[0])
    dz = min(hi_i[2], hi_j[2]) - max(lo_i[2], lo_j[2])
    if dx <= 0.0 or dz <= 0.0:
        return 0.0
    return float(dx * dz)


def resting_pairs(geom: SceneGeometry, contact_eps: float) -> list[tuple[int, int, float]]:
    """Unified "what rests on what" graph: (top_idx, bottom_idx, xz_overlap_area)
    for every ordered pair where top's lowest Y is within `contact_eps` of
    bottom's highest Y and their XZ footprints overlap with positive area.

    `contact_eps` is the caller's contact tolerance (support.py uses 1e-3,
    constraints.py 2e-2 -- both legitimate for their purposes; this helper
    guarantees they agree on *topology* given the same eps). Knife-edge
    contacts with zero XZ overlap area are not reported (measure zero).
    """
    n = geom.n
    if n < 2:
        return []
    bottom_y = geom.aabb_min[:, 1]
    top_y = geom.aabb_max[:, 1]
    close = np.abs(bottom_y[:, None] - top_y[None, :]) <= contact_eps  # [top, bottom]
    np.fill_diagonal(close, False)
    out: list[tuple[int, int, float]] = []
    for t, b in zip(*np.nonzero(close)):
        area = xz_overlap_area(geom, int(t), int(b))
        if area > 0.0:
            out.append((int(t), int(b), area))
    return out


def on_floor(geom: SceneGeometry, contact_eps: float) -> np.ndarray:
    """Boolean (n,) mask: object's lowest Y is within `contact_eps` of the
    container floor plane."""
    if geom.n == 0:
        return np.zeros(0, dtype=bool)
    return np.abs(geom.aabb_min[:, 1] - geom.container_floor_y) <= contact_eps
