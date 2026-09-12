# Examples

```sh
python3 -m physics validate examples/scene_carry_on.json --pretty
python3 -m physics validate examples/scene_carry_on.json --placements examples/placements_collision.json
python3 -m physics scan-to-object examples/scanned_item.json
python3 -m physics example   # prints scene_carry_on.json's shape, generated fresh
```

## Schema facts

- Units: **meters** everywhere in `Scene`/`Object`/`Container` JSON. The LiDAR
  scanner's `ScannedItem` is the one exception -- `width`/`depth`/`height`
  there are **centimetres**; `object_from_scanned_item` divides by 100.
- Coordinates: X = right, Y = **up**, Z = forward (right-handed, matches ARKit).
- Rotation: quaternion `[x, y, z, w]`, unit length. Absent/`null` -> identity
  `[0, 0, 0, 1]`.
- `dimensions = [x_extent, y_extent, z_extent]` -- local x/y/z *before*
  rotation. `y` is the vertical extent when unrotated, so a `ScannedItem`'s
  `width` -> dims[0], `height` -> dims[1], `depth` -> dims[2].
- Placements accept either `position`/`rotation` or `target_position`/
  `target_rotation` keys (both spellings appear in the team docs).
