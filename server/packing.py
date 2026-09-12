"""Scanned Mongo documents -> packer3d scenario -> solved layout in the team frame.

This is the ONE place the scan/solver conversion lives. The axis mapping itself is
not reimplemented here: `physics.packer3d_adapter` already defines it, the Swift
port mirrors the identical formulas, and a second copy would be a second thing to
keep in lockstep.

Frames
------
scan / team contract (SCAN_OUTPUT.md): x = right, y = up, z = forward, metres.
    `ScannedItem.dimensions` is [width, height, depth] in that order.
packer3d:                              x = length, y = width, z = UP, metres.

Extents map between the two by swapping the last two components (`swap_yz`, its own
inverse), so a scan's [w, h, d] is packer3d's (L, W, H) = (w, d, h). Points map by
`physics_point(x, y, z) = (x, z, -y)`. Both come from the adapter.

Mass
----
A scan measures shape, never weight, and packer3d's centre-of-mass balancing plus
`max_mass` are meaningless without it -- with mass 0 everywhere the solver silently
falls back to the volume centroid and `balance.py` has nothing to swap. So mass is
ESTIMATED from bounding-box volume and the detected rigidity (see `DENSITY_KG_M3`).
These are apparent densities of the whole bounding box, not of the material: a
folded shirt is mostly air, a camera mostly not. They are rough on purpose, and any
document carrying a real `massKg` overrides the estimate. Every response says which
items were estimated so the numbers are never mistaken for measurements.

Rigidity
--------
The scanner returns three values and packer3d has one boolean, so the mapping is
lossy in one direction only -- `fragile` is the boolean, and the other two differ by
density. Crucially the original rigidity is echoed back per placement rather than
discarded, because the physics layer DOES model compressibility
(`physics/compressibility.py`) and needs the soft/rigid distinction the solver
cannot use.

What is deliberately NOT modelled: soft items compress, and a box packer cannot
represent that -- a fleece is packed as an incompressible box of its scanned size.
That understates how much fits. It is the single biggest approximation here.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterable, Optional

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "packer3d")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from packer3d import Container, Item, OptimizerConfig, pack_optimized, verify  # noqa: E402
from physics.packer3d_adapter import physics_point, rotation_from_orientation, swap_yz  # noqa: E402

# Apparent density of an item's whole bounding box, kg/m^3. Travel goods, measured
# generously: the box of a folded garment is mostly air (~150), a shoe or a book
# averages a few hundred, and fragile things (cameras, glass, full toiletry bottles)
# run denser. Used only when a document has no real `massKg`.
DENSITY_KG_M3 = {"soft": 150.0, "rigid": 350.0, "fragile": 400.0}
DEFAULT_RIGIDITY = "rigid"

# The convention every /pack response is expressed in.
CONVENTION = (
    "x=right, y=up, z=forward, metres (SCAN_OUTPUT.md team contract). "
    "Container floor at y=0 spanning x in [0, width], z in [-depth, 0]; "
    "object position is its CENTRE; rotation is a quaternion (x, y, z, w)."
)


def estimate_mass(dimensions: Iterable[float], rigidity: str) -> float:
    """Bounding-box volume times an apparent density for `rigidity`. Never exact."""
    w, h, d = (float(v) for v in dimensions)
    return w * h * d * DENSITY_KG_M3.get(rigidity, DENSITY_KG_M3[DEFAULT_RIGIDITY])


def _rigidity_of(doc: dict) -> str:
    r = doc.get("rigidity")
    return r if r in DENSITY_KG_M3 else DEFAULT_RIGIDITY


def item_from_doc(doc: dict, *, keep_upright_when_fragile: bool = False) -> tuple[Item, dict]:
    """One scanned document -> (packer3d Item, metadata echoed back in the response).

    The item is its scanned bounding box. The stored heightmap is not used: packer3d
    can ingest one via `Item.from_scanned_heightmap`, which is stricter about what may
    stack, but a bounding box is the conservative reading of a 2.5D scan and keeps the
    solve fast. Raises ValueError if the document has no usable dimensions.
    """
    dims = doc.get("dimensions")
    if not isinstance(dims, (list, tuple)) or len(dims) != 3:
        raise ValueError(f"item {doc.get('id')!r}: dimensions must be [width, height, depth]")
    dims = [float(v) for v in dims]
    if min(dims) <= 0:
        raise ValueError(f"item {doc.get('id')!r}: dimensions must all be positive, got {dims}")

    rigidity = _rigidity_of(doc)
    fragile = rigidity == "fragile"
    measured = doc.get("massKg")
    estimated = measured is None
    mass = estimate_mass(dims, rigidity) if estimated else float(measured)

    length, width, height = swap_yz(dims)  # scan [w, h, d] -> packer3d (L, W, H)
    item = Item.box(
        str(doc["id"]), length, width, height,
        mass=mass,
        # packer3d `fragile` means "nothing may rest on it" -- the load-bearing sense,
        # which is exactly what the scanner's `fragile` implies.
        fragile=fragile,
        # Orientation is left free even for fragile items: "breaks if crushed or
        # dropped" says nothing about which way up, and locking it costs the solver
        # four of six orientations. Flip this on if a scan ever detects up-ness.
        keep_upright=fragile and keep_upright_when_fragile,
    )
    meta = {
        "label": doc.get("label"),
        "rigidity": rigidity,
        "massKg": round(mass, 4),
        "massEstimated": estimated,
    }
    return item, meta


def _container_from_spec(dims: Iterable[float], max_mass: Optional[float]) -> Container:
    w, h, d = (float(v) for v in dims)
    if min(w, h, d) <= 0:
        raise ValueError("container dimensions must be three positive metres [width, height, depth]")
    length, width, height = swap_yz((w, h, d))
    kwargs: dict[str, Any] = {}
    if max_mass is not None:
        kwargs["max_mass"] = float(max_mass)
    return Container("suitcase", (length, width, height), **kwargs)


def _placement_out(p: dict, meta: dict[str, dict]) -> dict:
    """packer3d placement -> team-frame placement, with the scan metadata re-attached."""
    m = meta.get(p["item_id"], {})
    return {
        "id": p["item_id"],
        "label": m.get("label"),
        "rigidity": m.get("rigidity"),
        "massKg": m.get("massKg"),
        "massEstimated": m.get("massEstimated"),
        # `dims` already has the orientation permutation applied, so these are the
        # world-aligned extents; the quaternion carries the permutation for a renderer
        # that would rather place the item by its own unoriented dimensions.
        "dimensions": list(swap_yz(p["dims"])),
        "position": list(physics_point(*p["center"])),
        "rotation": list(rotation_from_orientation(p.get("orientation", "xyz"))),
    }


def pack_documents(
    docs: Iterable[dict],
    container_dims: Iterable[float],
    *,
    max_mass: Optional[float] = None,
    time_budget_s: float = 3.0,
    seed: int = 0,
) -> dict:
    """Solve a layout for `docs` and return it in the team frame.

    The result is the optimised layout only -- never the `--compare` wrapper -- with
    `unpacked` carrying packer3d's own per-item reason so the app can say why
    something did not fit.
    """
    items, meta = [], {}
    for doc in docs:
        item, m = item_from_doc(doc)
        items.append(item)
        meta[item.id] = m
    if not items:
        raise ValueError("no items to pack")

    container = _container_from_spec(container_dims, max_mass)
    result = pack_optimized(
        container, items,
        OptimizerConfig(time_budget_s=float(time_budget_s), seed=seed),
    )
    raw = result.to_dict()

    w, h, d = (float(v) for v in container_dims)
    return {
        "convention": CONVENTION,
        "container": {"dimensions": [w, h, d], "maxMassKg": max_mass},
        "placements": [_placement_out(p, meta) for p in raw.get("placements", [])],
        "unpacked": [
            {"id": u["id"], "label": meta.get(u["id"], {}).get("label"), "reason": u.get("reason")}
            for u in raw.get("unpacked", [])
        ],
        "metrics": raw.get("metrics", {}),
        "approximations": [
            "Mass is estimated from bounding-box volume and rigidity unless a document "
            "carries massKg; per-item massEstimated says which.",
            "Soft items are packed as incompressible boxes of their scanned size, so a "
            "real suitcase holds more than this layout suggests.",
            "Items are packed as scanned bounding boxes; the stored heightmap is not used.",
        ],
        # Independent geometric re-check of the solver's own output. Empty means the
        # layout is self-consistent; anything here is a solver bug worth surfacing.
        "verifyErrors": verify(result, items),
    }
