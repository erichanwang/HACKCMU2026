# LiDAR hull footprints

## What a footprint is

`Object.footprint` (see `physics/schema.py`) is an optional convex polygon in
the object's LOCAL (x, z) plane, in **meters**, straight from the LiDAR
scan's 2D convex hull. Every point must lie within `+-dimensions[0]/2 x
+-dimensions[2]/2` -- the object becomes a convex PRISM (the footprint
extruded over `dimensions[1]`) instead of a box, but `dimensions` still
bounds it, so box-only consumers stay correct. `None` = plain box.

## Why it matters

A box always over-approximates a scanned item. `tests/fixtures.py`'s
`SHOE_FOOTPRINT` (tapered hexagon: narrow toe, wide heel, widest at the ball
of the foot) has area 0.027550 m^2 against its box's 0.145*2 * 0.06*2 =
0.034800 m^2 -- 20.8% of the box is empty air the box still charges the
packer for. Two items whose boxes overlap can be physically clear if neither
hull reaches into the overlap; see `tests.fixtures.scene_hull_clears_box_would_not`.

## JSON shape

`Scene`/`Object` JSON (`physics.io.scene_to_dict`/`object_to_dict`):
```json
{"id": "shoe", "dimensions": [0.29, 0.11, 0.12], "position": [...],
 "footprint": [[-0.145, -0.02], [0.0, -0.06], [0.145, -0.05], ...], ...}
```
`footprint` is `null` for a plain box. The phone's `ScannedItem` (`SCAN_OUTPUT.md`) carries
the same shape in **metres**, like its `dimensions`, relative to the box centre (width +x,
depth +z), at most 16 vertices:
```json
{"id": "shoe-1", "dimensions": [0.29, 0.11, 0.12],
 "footprint": [[-0.145, -0.02], [0.0, -0.06], [0.145, -0.05], ...]}
```
(The older centimetre form `width`/`depth`/`height` + a cm `footprint` is still read.)

## Python entry points

- `object_from_scanned_item(item, ...)`: reads `item["footprint"]` (local; metres with
  the `dimensions` form, cm with the `width`/`depth`/`height` form) into `Object.footprint`.
- `object_from_box_fit(id, width_m, depth_m, height_m, center, axis, *,
  hull_xz_world=...)`: maps the scan's WORLD-space hull (the spike's
  `convexHull(flat)` output) into the object's local frame via the same
  rotation matrix as the object's pose (`quat_to_matrix`, columns 0 and 2)
  -- never hand-rolled trig, so it can't disagree with the quaternion. A
  point overshooting the half-dimensions by <= 1mm is clamped; by more,
  raises `ValueError` naming the object id.
- CLI: `python3 -m physics scan-to-object item.json --footprint-from-hull
  hull.json` (a `BoxFit` dump: `width`/`depth`/`height` in m, `center`,
  `axis`, plus `hull`) builds a posed `Object` with a footprint directly,
  instead of the pose-less `ScannedItem` path.

## iOS side

Implemented: `Spike/Geometry.swift` derives `ScannedItem.footprint` from the cluster's
XZ convex hull with the sign convention of `object_from_box_fit` above (local x = projection
onto `axis`, local z = onto `(-axis.z, axis.x)`), decimated to at most 16 vertices, in
metres. `tests/swift/hull/` covers a rotated rectangle, an L-shape, a circle and degenerate
clusters. Do not multiply by 100: `dimensions` are metres, so a cm footprint next to them is
a silent 100x frame mismatch.
