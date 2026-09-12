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
`footprint` is `null` for a plain box. `ScannedItem` carries the same shape
in **centimetres**, relative to the box centre (width +x, depth +z):
```json
{"id": "shoe-1", "width": 29.0, "depth": 12.0, "height": 11.0,
 "footprint": [[-14.5, -2.0], [0.0, -6.0], [14.5, -5.0], ...]}
```

## Python entry points

- `object_from_scanned_item(item, ...)`: reads `item["footprint"]` (cm,
  local) and converts to metres.
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

## Suggested iOS spike change

`Spike/Geometry.swift`'s `fitBox` already computes the hull and discards it.
Keep it, and derive `ScannedItem.footprint` with the SAME sign convention as
`object_from_box_fit` above -- local x = the hull point's projection onto
`axis`; local z = onto `axis` rotated +90 deg in (x, z), i.e. `(-axis.z,
axis.x)` (matching `quat_to_matrix`'s column 2 for our right-handed frame):

```swift
struct BoxFit {
    // ...existing fields...
    var hull: [SIMD2<Float>]  // world-space (x, z), from convexHull(flat)
}
// fitBox: unchanged, just also return `hull: convexHull(flat)` in BoxFit.

extension ScannedItem {
    init(_ box: BoxFit) {
        width = box.width * 100; depth = box.depth * 100; height = box.height * 100
        let u = SIMD2(box.axis.x, box.axis.z), v = SIMD2(-u.y, u.x)
        let c = SIMD2(box.center.x, box.center.z)
        footprint = box.hull.map { p -> [Float] in
            let d = p - c
            return [simd_dot(d, u) * 100, simd_dot(d, v) * 100]  // -> cm
        }
    }
}
```
Add `var footprint: [[Float]]?` to `ScannedItem` -- `Codable` picks it up for
free. The Swift `PackPhysics` port landing on `main` will accept this same
JSON once it exists; no format change needed on that side.
