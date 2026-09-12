"""Adapter: the packing solver's (`packer3d/`) JSON <-> `physics.schema` scenes.

`packer3d` is the teammate's module (read-only here). Its output contract is
`packer3d/README.md` ("Output JSON contract" + "Coordinates").

Frames
------
packer3d: right-handed, x = length (L), y = width (W), z = UP (H), origin at the
    container's MIN corner. `placement.position` is the MIN corner of the oriented
    bbox, `dims` the oriented bbox (the orientation permutation is already applied),
    `center = position + dims/2`.
physics:  right-handed, X = right, Y = UP, Z = forward, meters, container centred at
    its `position`, object `dimensions` along local axes, quaternion (x, y, z, w).

The proper rotation (det +1) that sends packer z -> physics Y and packer y -> physics -Z is

    physics_point(x, y, z) = (x, z, -y)

so (identical formulas are used by the Swift port -- do not deviate):

* container -> `Container(id, dimensions=(L, H, W), position=(L/2, H/2, -W/2))`; its floor
  is at Y = 0, its min-x wall at X = 0, its Z extent is [-W, 0].
* placement -> `Object(id=item_id, dimensions=(dx, dz, dy), position=(cx, cz, -cy),
  rotation=identity, mass_kg=mass)` with `(cx, cy, cz) = center`, `(dx, dy, dz) = dims`.
  Exact for boxes: packer3d only permutes axes, so an axis-aligned bbox stays axis-aligned
  and the rotation is always identity. `scene_from_packer3d(..., oriented=False)` gives the
  other (equivalent) form -- own dims + the orientation as the pose; see its docstring.

Deliberate approximations (call these out, don't hide them)
----------------------------------------------------------
* CYLINDERS are packed by packer3d as their bounding box, so they are mapped as that
  box too -- conservative (the box contains the cylinder). The real cylinder fields
  (`shape`/`radius`/`height`/`axis`) are handed back in `extras["shapes"]` for the
  renderer instead of being lost.
* A CYLINDRICAL CONTAINER (the Dragon capsule) becomes its bounding box `(2R, 2R, H)`,
  because `physics.schema.Container` is a box. That makes our containment check WEAKER
  than packer3d's for such containers -- packer3d's curved-wall test is the authority
  there. Box containers (suitcase) are exact.
* MICROGRAVITY (`container.gravity == false`) is not representable in `physics.schema`:
  the physics layer always pulls along -Y. A microgravity layout therefore reports
  UNSUPPORTED_OBJECT for every floating item; that is expected, not a solver bug.
  See `docs/SOLVER_INTEGRATION.md`.
* A NESTED PLACEMENT (an item scanned with a `heights` grid, e.g. a bowl, with a
  smaller item resting in its real cavity) would otherwise misreport as
  OBJECT_COLLISION here: a `placement` only ever carries one bbox, so mapping it
  straight to one `Object` treats the bowl as solid all the way up to its rim, even
  though `packer3d`'s own decoder/verify() already pack/accept the nesting.
  `_objects_from_placement` decomposes such a placement into several `Object`s (one
  per carved cavity cell, mirroring `packer3d.models.Item.solid_boxes()`) so this
  layer agrees. See its docstring and `tests/test_packer3d_adapter.py`'s nesting test.

Constraint mapping
------------------
* packer3d `fragile` = "nothing may rest on it" -> `Constraints(fragile=True,
  cannot_support_weight=True)`.
* `keep_upright` / `priority` / the pre-`count`-expansion id live in the *scenario*, not in
  a placement, so every entry point takes an optional `items=` (scenario dict or its
  `items` list) to pull them: `keep_upright=True` with `orientation_lock=None`
  (a 90-degree-rotation solver never tilts anything, so no lock is needed).
* obstacles -> objects with id `f"obstacle:{id}"`, `mass_kg=0.0`, rigid, so collisions
  against them are caught by the normal collision pass.
* `result.unpacked` items are NOT in the scene; they come back in `extras["unpacked"]`.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Optional

import numpy as np

from physics.schema import Constraints, Container, Object, Scene
from physics.validator import validate_layout

IDENTITY_ROTATION = (0.0, 0.0, 0.0, 1.0)

# `scene_from_packer3d_scenario` table layout: clear space between the container's
# +x wall and the first item, and the gap between neighbouring items (meters).
TABLE_GAP_M = 0.15
TABLE_SPACING_M = 0.05


def physics_point(x, y, z) -> tuple[float, float, float]:
    """packer3d point -> physics point: (x, y, z) -> (x, z, -y)."""
    return (float(x), float(z), -float(y))


def packer3d_point(X, Y, Z) -> tuple[float, float, float]:
    """physics point -> packer3d point (inverse of `physics_point`)."""
    return (float(X), -float(Z), float(Y))


def swap_yz(d) -> tuple[float, float, float]:
    """(a, b, c) -> (a, c, b). Maps extents both ways (it is its own inverse)."""
    return (float(d[0]), float(d[2]), float(d[1]))


# --- orientation -> quaternion ------------------------------------------------
# packer3d orientation name -> axis permutation: world axis k takes the item's own
# axis perm[k] (`packer3d.models.BOX_ORIENTATIONS`). A cylinder's bbox permutes the
# same way, since its item frame is (2r, 2r, h) with the axis along item z.
_PERM = {
    "xyz": (0, 1, 2), "xzy": (0, 2, 1), "yxz": (1, 0, 2),
    "yzx": (1, 2, 0), "zxy": (2, 0, 1), "zyx": (2, 1, 0),
    "cyl_axis_z": (0, 1, 2), "cyl_axis_x": (2, 1, 0), "cyl_axis_y": (0, 2, 1),
}
# packer3d basis -> physics basis, i.e. the matrix of `physics_point`.
_P = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])


def _quat_from_matrix(m: np.ndarray) -> tuple[float, float, float, float]:
    """Rotation matrix -> (x, y, z, w) (Shepperd's method, largest-component branch)."""
    t = float(np.trace(m))
    if t > 0.0:
        s = math.sqrt(t + 1.0) * 2.0
        return ((m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, 0.25 * s)
    i = int(np.argmax(np.diag(m)))
    j, k = (i + 1) % 3, (i + 2) % 3
    s = math.sqrt(1.0 + m[i, i] - m[j, j] - m[k, k]) * 2.0
    q = [0.0, 0.0, 0.0]
    q[i], q[j], q[k] = 0.25 * s, (m[j, i] + m[i, j]) / s, (m[k, i] + m[i, k]) / s
    return (q[0], q[1], q[2], (m[k, j] - m[j, k]) / s)


def rotation_from_orientation(orientation: str) -> tuple[float, float, float, float]:
    """packer3d `placement.orientation` -> physics quaternion (x, y, z, w).

    Needed whenever the object carries its OWN (unoriented) dimensions and only the
    pose moves it -- `placements_from_packer3d` + `apply_placements`, and every
    `pan.solver_bridge` action. (`scene_from_packer3d` instead builds objects straight
    from the already-oriented `placement.dims`, so those keep identity rotation; both
    representations produce the same world OBB, which the tests assert.)

    Derivation: the permutation matrix `M[k, perm[k]] = 1` maps an item-frame vector to
    the packer world frame. An odd permutation has det -1 (a reflection, not a rotation),
    so one column is negated -- a 180-degree flip, which leaves a box's (symmetric)
    extents untouched and makes det +1. The physics-frame rotation is then
    `R = P M P^T` with `P` = the matrix of `physics_point`.
    """
    perm = _PERM[str(orientation)]
    m = np.zeros((3, 3))
    for k, src in enumerate(perm):
        m[k, src] = 1.0
    if np.linalg.det(m) < 0.0:
        m[:, 0] *= -1.0
    return _quat_from_matrix(_P @ m @ _P.T)


def _unoriented_dims(dims, orientation: str) -> tuple[float, float, float]:
    """Undo the solver's axis permutation: the oriented bbox `placement["dims"]` -> the
    item's OWN extents. `M` above puts item axis `perm[k]` on world axis `k`, so extents
    map back with `own[perm[k]] = dims[k]`. Pairs with `rotation_from_orientation`."""
    own = [0.0, 0.0, 0.0]
    for k, src in enumerate(_PERM[str(orientation)]):
        own[src] = float(dims[k])
    return (own[0], own[1], own[2])


# --- result / scenario plumbing ----------------------------------------------


def _pick(result: dict, strategy: Optional[str]) -> tuple[dict, str]:
    """Resolve a single-strategy result out of `result`.

    Accepts a single-strategy result dict, or the `--compare` wrapper
    `{"naive": {...}, "optimized": {...}}` -- in which case `strategy` must say
    which one to use, otherwise `ValueError` (the caller can equally well index
    `result["naive"]` / `result["optimized"]` itself).
    """
    if "placements" in result:
        got = result.get("strategy")
        if strategy is not None and got is not None and strategy != got:
            raise ValueError(f"result is strategy {got!r}, not {strategy!r}")
        return result, str(got or "unknown")
    keys = [k for k, v in result.items() if isinstance(v, dict) and "placements" in v]
    if not keys:
        raise ValueError("not a packer3d result: no 'placements' at top level or one level down")
    if strategy is None:
        raise ValueError(
            f"this is a --compare result with strategies {keys}: pass strategy=<name> "
            f"(or index it yourself, e.g. result[{keys[0]!r}])"
        )
    if strategy not in keys:
        raise ValueError(f"unknown strategy {strategy!r}; this result has {keys}")
    return result[strategy], strategy


def item_metadata(items) -> dict[str, dict]:
    """`{expanded_id: {"source_id", "keep_upright", "priority", ...}}` from a scenario
    dict (or just its `items` list). `count: n` expands to `id_1 .. id_n`, exactly like
    `packer3d.scenario.load_scenario`.

    An entry with a lidar `heights` grid (SCAN_OUTPUT.md) also carries it through,
    normalized to `heights`, `cellSize`, `width`, `depth`, `height` + `units` ("cm" or
    "m") -- `packer3d.models.Item.from_scanned_heightmap` accepts either the raw
    cm `width`/`depth`/`height` keys or the server document form
    (`dimensions: [width, height, depth]`, metres); this reads whichever is present
    the same way. `_objects_from_placement` uses it to decompose that item's
    placement into its real cavity shape. An entry with `heights` but neither form of
    dims present is malformed for `from_scanned_heightmap` too, so it's just left
    without cavity data here (falls back to the plain single-box `Object`).
    """
    if items is None:
        return {}
    entries = items.get("items", []) if isinstance(items, dict) else items
    meta: dict[str, dict] = {}
    for d in entries:
        n = int(d.get("count", 1))
        ids = [d["id"]] if n == 1 else [f"{d['id']}_{k + 1}" for k in range(n)]
        for iid in ids:
            entry = {
                "source_id": d["id"],
                "keep_upright": bool(d.get("keep_upright", False)),
                "priority": float(d.get("priority", 1.0)),
            }
            if "heights" in d:
                width, depth, height, units = d.get("width"), d.get("depth"), d.get("height"), "cm"
                if width is None and "dimensions" in d:
                    width, height, depth = d["dimensions"]  # server document form, metres
                    units = "m"
                if width is not None and depth is not None and height is not None:
                    entry.update(
                        heights=d["heights"], cellSize=d.get("cellSize", 0.0),
                        width=width, depth=depth, height=height, units=units,
                    )
            meta[iid] = entry
    return meta


def _constraints(fragile: bool, keep_upright: bool) -> Constraints:
    return Constraints(
        fragile=bool(fragile),
        cannot_support_weight=bool(fragile),  # packer3d fragile == nothing may rest on it
        keep_upright=bool(keep_upright),
        orientation_lock=None,
    )


def _container(id: str, dims) -> Container:
    L, W, H = (float(v) for v in dims)
    return Container(id=id, dimensions=(L, H, W), position=(L / 2.0, H / 2.0, -W / 2.0))


def _object_from_placement(p: dict, meta: dict[str, dict], oriented: bool = True) -> Object:
    m = meta.get(p["item_id"], {})
    o = str(p.get("orientation", "xyz"))
    return Object(
        id=p["item_id"],
        dimensions=swap_yz(p["dims"] if oriented else _unoriented_dims(p["dims"], o)),
        position=physics_point(*p["center"]),
        rotation=IDENTITY_ROTATION if oriented else rotation_from_orientation(o),
        mass_kg=float(p.get("mass", 0.0)),
        constraints=_constraints(p.get("fragile", False), m.get("keep_upright", False)),
    )


# packer3d orientation name -> axis permutation, restricted to the two that keep a
# scanned height grid's "up" pointing along world z (`packer3d.models.oriented_solid_boxes`).
_CAVITY_PERM = {"xyz": (0, 1, 2), "yxz": (1, 0, 2)}


def _cavity_local_boxes(entry: dict, max_blocks: int = 4) -> list[tuple[tuple, tuple]]:
    """Local `(lo, hi)` boxes (packer3d item frame: x=width, y=depth, z=height, metres)
    approximating a scanned item's real cavity, via the same "max-pool `heights` down to
    <= max_blocks x max_blocks cells, floor to tallest point per cell" decomposition as
    `packer3d.models.Item.solid_boxes()`. A cell at height ~0 gets no box -- SCAN_OUTPUT.md's
    heightmap contract already treats that as "nothing observed here" for every other
    consumer of `heights`, so this doesn't add any new ambiguity, it just also skips solid
    there. Deliberately duplicated (not imported from `packer3d`): the decoder that owns
    this algorithm is still being changed by another session, and only the pure geometry is
    needed here, not the solver's internals. Returns `[]` if `entry` has no usable grid.
    """
    heights = entry.get("heights")
    if not heights or float(entry.get("cellSize", 0.0)) <= 0.0:
        return []
    scale = 0.01 if entry.get("units", "cm") == "cm" else 1.0  # cm or metres, see `item_metadata`
    arr = np.asarray(heights, dtype=float) * scale
    rows, cols = arr.shape
    dx, dy, dz = (float(entry[k]) * scale for k in ("width", "depth", "height"))
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
            if h > 1e-9:
                boxes.append(((x0, y0, 0.0), (x1, y1, min(h, dz))))
            y0 = y1
        x0 = x1
    return boxes


def _objects_from_placement(p: dict, meta: dict[str, dict], oriented: bool = True) -> list[Object]:
    """Usually one `Object` per placement (`_object_from_placement`). But when the item
    has a scanned `heights` grid (`item_metadata`) and the solver kept it z-up
    (`orientation` "xyz"/"yxz"), several -- one per carved cavity cell -- so a nested
    placement (a cup resting in a bowl's interior, `packer3d/tests/test_nesting.py`)
    doesn't collide against the bowl's full outer bounding box here the way
    `packer3d`'s own decoder/verify() no longer do once they decompose it (see module
    docstring; this is that same decomposition, applied on this side of the JSON
    boundary since a placement only ever carries one bbox).

    All of the item's mass goes on its single biggest solid (id = the plain item id);
    the rest are massless (id `f"{item_id}#{k}"`) so total mass is exactly what it was
    before decomposition -- `center_of_mass` shifts slightly (mass now sits at that one
    solid's centroid instead of the full bbox's), an accepted approximation, since a
    uniform-density assumption was already only a guess for an irregular scanned item.
    Every solid gets the item's own constraints, so nothing may rest on any part of a
    fragile item, same as before.
    """
    # The cavity solids below are built in world space with identity rotation, i.e. the
    # `oriented=True` form only. In the unoriented form the caller wants the item's own
    # dimensions plus a rotation, and applying the permutation to already-permuted cavity
    # boxes would double-rotate exactly as this module's docstring warns. Fall back.
    if not oriented:
        return [_object_from_placement(p, meta, oriented)]
    m = meta.get(p["item_id"], {})
    local_boxes = _cavity_local_boxes(m) if m.get("heights") else []
    perm = _CAVITY_PERM.get(p.get("orientation"))
    if not local_boxes or perm is None:
        return [_object_from_placement(p, meta, oriented)]

    position = [float(v) for v in p["position"]]
    constraints = _constraints(p.get("fragile", False), m.get("keep_upright", False))
    volumes = [(hi[0] - lo[0]) * (hi[1] - lo[1]) * (hi[2] - lo[2]) for lo, hi in local_boxes]
    heaviest = volumes.index(max(volumes))
    objects = []
    for k, (lo, hi) in enumerate(local_boxes):
        wlo = tuple(position[a] + lo[perm[a]] for a in range(3))
        whi = tuple(position[a] + hi[perm[a]] for a in range(3))
        center = tuple((wlo[a] + whi[a]) / 2.0 for a in range(3))
        objects.append(Object(
            id=p["item_id"] if k == heaviest else f"{p['item_id']}#{k}",
            dimensions=swap_yz(tuple(whi[a] - wlo[a] for a in range(3))),
            position=physics_point(*center),
            rotation=IDENTITY_ROTATION,
            mass_kg=float(p.get("mass", 0.0)) if k == heaviest else 0.0,
            constraints=constraints,
        ))
    return objects


def _object_from_obstacle(ob: dict) -> Object:
    lo, d = [float(v) for v in ob["position"]], [float(v) for v in ob["dims"]]
    center = [lo[k] + d[k] / 2.0 for k in range(3)]
    return Object(
        id=f"obstacle:{ob['id']}",
        dimensions=swap_yz(d),
        position=physics_point(*center),
        rotation=IDENTITY_ROTATION,
        mass_kg=0.0,
        rigidity="rigid",
    )


# --- public API ---------------------------------------------------------------


def scene_from_packer3d(
    result: dict,
    *,
    items: Optional[list[dict]] = None,
    include_obstacles: bool = True,
    strategy: Optional[str] = None,
    oriented: bool = True,
) -> tuple[Scene, dict]:
    """packer3d result -> (`Scene`, `extras`).

    `result` is a single-strategy result or the `--compare` wrapper (then pass
    `strategy="naive"`/`"optimized"`). `items` is the scenario (dict or its `items`
    list) and only supplies `keep_upright` / `priority` / pre-expansion ids.

    `oriented` picks which of the two equivalent forms the placed objects take. Both
    occupy exactly the same world box, so the validator's verdict is the same:

    * `True` (default) -- the solver's already-oriented `dims` with identity rotation.
      Axis-aligned and exact, so this stays the canonical form for everything that only
      GRADES a finished layout (`validate_packer3d`, `physics/__main__.py`,
      `server/planner.py`): those never apply placements on top.
    * `False` -- the item's own dims with the orientation carried in the pose, i.e. the
      same form `scene_from_packer3d_scenario` builds. Use this, and only this, when the
      scene is then moved by `placements_from_packer3d` (`physics.io.apply_placements`,
      `physics.pan`, `pan.solver_bridge`); pairing those rotations with the default
      oriented dims applies the permutation twice and the physics gate rejects the
      solver's own valid plan (FIXES.md section 2).

    `extras = {"unpacked": [...], "shapes": {id: cylinder fields}, "strategy": str,
    "metrics": result["metrics"], "items": item_metadata(items)}`.
    """
    single, strategy_name = _pick(result, strategy)
    meta = item_metadata(items)
    placements = single.get("placements", [])
    container_d = single["container"]

    objects = [obj for p in placements for obj in _objects_from_placement(p, meta, oriented)]
    if include_obstacles:
        objects += [_object_from_obstacle(ob) for ob in container_d.get("obstacles", [])]

    shapes = {
        p["item_id"]: {k: p[k] for k in ("shape", "radius", "height", "axis") if k in p}
        for p in placements
        if p.get("shape") == "cylinder"
    }
    extras = {
        "unpacked": list(single.get("unpacked", [])),
        "shapes": shapes,
        "strategy": strategy_name,
        "metrics": single.get("metrics", {}),
        "items": meta,
    }
    return Scene(container=_container(container_d.get("id", "container"), container_d["dims"]), objects=objects), extras


def placements_from_packer3d(result: dict, *, strategy: Optional[str] = None) -> list[dict]:
    """packer3d placements -> OUR placement shape `[{"id", "position", "rotation"}]`,
    ready for `physics.io.validate(scene, placements)` / `apply_placements` (the
    "scene of scanned items + solver placements" flow).

    `rotation` is `rotation_from_orientation(placement["orientation"])`, NOT identity:
    those objects carry their own unoriented dimensions, so the solver's axis
    permutation has to travel in the pose or the item lands rotated 90 degrees wrong.

    So the scene these are applied to must be in the own-dims form --
    `scene_from_packer3d_scenario(scenario)` or `scene_from_packer3d(..., oriented=False)`,
    never the default oriented scene (that double-rotates; see `scene_from_packer3d`).
    """
    single, _ = _pick(result, strategy)
    return [
        {
            "id": p["item_id"],
            "position": list(physics_point(*p["center"])),
            "rotation": list(rotation_from_orientation(p.get("orientation", "xyz"))),
        }
        for p in single.get("placements", [])
    ]


def packer3d_placement_from_object(obj: Object) -> dict:
    """Inverse of `_object_from_placement`: physics `Object` -> packer3d placement dict.

    Exact round trip for an axis-aligned box (identity rotation); `obj.rotation` is
    ignored, since packer3d has no way to express a non-axis-aligned placement.
    """
    dims = swap_yz(obj.dimensions)
    center = packer3d_point(*obj.position)
    return {
        "item_id": obj.id,
        "shape": "box",
        "position": [center[k] - dims[k] / 2.0 for k in range(3)],
        "dims": list(dims),
        "center": list(center),
        "orientation": "xyz",
        "mass": float(obj.mass_kg),
        "fragile": bool(obj.constraints.fragile),
    }


def validate_packer3d(
    result: dict,
    *,
    items: Optional[list[dict]] = None,
    strategy: Optional[str] = None,
    include_obstacles: bool = True,
) -> dict:
    """`validate_layout` on the mapped scene, plus `"adapter"`: the `extras` dict."""
    scene, extras = scene_from_packer3d(
        result, items=items, include_obstacles=include_obstacles, strategy=strategy
    )
    out = validate_layout(scene)
    out["adapter"] = extras
    return out


def _load_scenario(scenario):
    """`packer3d.scenario.load_scenario(scenario)`, importing packer3d from the sibling
    `packer3d/` source dir (the repo root only holds it as a namespace dir)."""
    pkg_root = Path(__file__).resolve().parent.parent / "packer3d"
    if (pkg_root / "packer3d" / "__init__.py").is_file() and str(pkg_root) not in sys.path:
        sys.path.append(str(pkg_root))  # a real package beats the namespace portion
    from packer3d.scenario import load_scenario  # noqa: PLC0415 (optional dependency)

    return load_scenario(scenario)


def scene_from_packer3d_scenario(scenario: dict) -> Scene:
    """The UNPACKED "current state" of a scenario: the container plus every item
    (with `count` expanded to `id_1 .. id_n` by `packer3d.scenario.load_scenario`)
    laid out OUTSIDE the container on the table -- what PAN's "before" observation
    renders.

    Layout: a row along +X, the first item's near face at `X = L + TABLE_GAP_M`, each
    next item `item length + TABLE_SPACING_M` further along; every item rests on the
    floor plane (`Y = dims_y/2`), is centred on the container's depth (`Z = -W/2`),
    has identity rotation and dimensions `(length, height, depth)`.
    """
    container, items, _config, _weights = _load_scenario(scenario)
    W = container.dims[1]
    x = container.dims[0] + TABLE_GAP_M  # min-x of the first item: everything is outside
    objects = []
    for it in items:
        d = swap_yz(it.dims)  # packer (l, w, h) -> our (length, height, depth)
        objects.append(
            Object(
                id=it.id,
                dimensions=d,
                position=(x + d[0] / 2.0, d[1] / 2.0, -W / 2.0),
                rotation=IDENTITY_ROTATION,
                mass_kg=float(it.mass),
                constraints=_constraints(it.fragile, it.keep_upright),
            )
        )
        x += d[0] + TABLE_SPACING_M
    return Scene(container=_container(container.id, container.dims), objects=objects)
