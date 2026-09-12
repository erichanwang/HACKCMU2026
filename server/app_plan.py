"""packer3d result -> the iOS app's plan JSON (`packing-core/Sources/PackingPlan/`).

Frames
------
packer3d (`packer3d/README.md` "Coordinates"): x = length, y = width, z = UP, origin at
    the container's MIN corner; `placement.position` is the MIN corner of the oriented
    bbox and `dims` is that oriented bbox (the permutation is already applied).
app bag frame, derived from the Swift:
  * `Geometry.swift`: "`x` is width, `y` is up, `z` is depth" and `plan.json`'s header,
    "Bag frame: origin at the inner floor corner of the interior".
  * `PackingPlan.swift`: a placement's `position` is the "**Min corner** of the item's
    bounding box, in metres, bag frame. Not the center", `size` is the "Full extent in
    bag axes ... already accounting for `rotation`", and `Container.interior` is
    "`[0, dimensions]` on each axis" -- so everything stays non-negative.

Both frames put the origin at the container's min corner and store min-corner + size, so
the whole mapping is the y/z swap, for points and extents alike:

    app(x, y, z) = (packer_x, packer_z, packer_y)

i.e. `physics/packer3d_adapter.py`'s `(x, y, z) -> (x, z, -y)` with the depth axis
re-signed: the physics frame puts the interior at Z in [-W, 0], the app at Z in [0, depth].
The suitcase document's `dimensions` ([width, height, depth]) is therefore the app
container's `dimensions` verbatim, while [width, depth, height] is the packer3d container.

Rotation
--------
`AxisRotation.swift`: "Read the raw string left to right as the item-local axes assigned
to bag **X, Y, Z** in that order", which is exactly packer3d's own orientation string
("world axis <- item axis") but in the packer frame. Both the world axes and the item's
own axes swap y/z -- a scanned item's packer dims are `(width, depth, height)`
(`Item.from_scanned_heightmap` -> `Item.box(id, width, depth, height)`) whereas its app
axes are X = width, Y = height, Z = depth -- so the string is re-read as
bag X <- packer x, bag Y <- packer z, bag Z <- packer y, with the item letters relabelled
x -> X, y -> Z, z -> Y. That gives the table below.
"""

_ROTATION = {"xyz": "XYZ", "xzy": "XZY", "yxz": "ZYX", "yzx": "ZXY", "zxy": "YZX", "zyx": "YXZ",
             # cylinders: packer3d's `height` runs along its own z (app Y) when upright, so
             # cyl_axis_z is identity; on its side the height axis goes to bag X or bag Z.
             "cyl_axis_z": "XYZ", "cyl_axis_x": "YXZ", "cyl_axis_y": "XZY"}


def _v(x, y, z) -> dict:
    return {"x": float(x), "y": float(y), "z": float(z)}


def _nested_in(placements: list) -> dict:
    """item_id -> the id of the placement whose scanned cavity it sits in, for placements whose
    bounding boxes interpenetrate. The solver nests an item only inside another's real cavity
    (packer3d/verify.py re-checks that with the same solid decomposition), so in a validated
    plan an overlap of two boxes means nesting, and the host is the larger box. An unvalidated
    plan can carry a genuine collision here too; `validation.valid` says which it is."""
    boxes = [(p["item_id"], [float(v) for v in p["position"]], [float(v) for v in p["dims"]]) for p in placements]
    volume = {i: d[0] * d[1] * d[2] for i, _, d in boxes}
    out = {}
    for i, lo, d in boxes:
        hosts = [j for j, jlo, jd in boxes
                 if j != i and volume[j] > volume[i]
                 and all(min(lo[k] + d[k], jlo[k] + jd[k]) - max(lo[k], jlo[k]) > 1e-9 for k in range(3))]
        out[i] = max(hosts, key=volume.__getitem__) if hosts else None
    return out


def to_app_plan(result: dict, suitcase: dict, items_by_id: dict) -> dict:
    """`PackResult.to_dict()` -> a `PackingPlan` JSON document for the app.

    `items_by_id` maps a packer3d `item_id` to the item document it came from (for `label`
    and `note`). `result["unpacked"]` is not part of a plan and is dropped here.
    """
    width, height, depth = (float(v) for v in suitcase["dimensions"])
    nested = _nested_in(result.get("placements", []))
    placements = []
    for step, p in enumerate(result.get("placements", []), start=1):
        item = items_by_id.get(p["item_id"], {})
        x, y, z = p["position"]
        dx, dy, dz = p["dims"]
        placements.append({
            "step": step,
            "itemId": p["item_id"],
            "label": item.get("label") or p["item_id"],
            "zone": "interior",
            "position": _v(x, z, y),
            "size": _v(dx, dz, dy),
            "rotation": _ROTATION.get(p.get("orientation"), "XYZ"),
            "note": item.get("description") or "",
            "nestedIn": nested[p["item_id"]],  # the host whose cavity holds this item, else null
        })
    return {
        "version": 1,
        "units": "meters",
        "container": {
            "id": str(suitcase["_id"]),
            "label": suitcase.get("name") or "Suitcase",
            "dimensions": _v(width, height, depth),
            # the solver has no notion of zones, so the whole interior is the one zone
            "zones": [{"id": "interior", "label": "Interior", "origin": _v(0, 0, 0),
                       "size": _v(width, height, depth)}],
        },
        "placements": placements,
    }
