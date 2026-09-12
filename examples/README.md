# Examples

```sh
python3 -m physics validate examples/scene_carry_on.json --pretty
python3 -m physics validate examples/scene_carry_on.json --placements examples/placements_collision.json
python3 -m physics example   # prints scene_carry_on.json's shape, generated fresh
```

`examples/scanned_item.json` is the phone's current scan payload (metres,
`dimensions`/`cellSize`/`heights`/`suitcaseId` -- see `SCAN_OUTPUT.md`).
`physics.io.object_from_scanned_item` (`python3 -m physics scan-to-object`)
is a separate, older adapter for a flat **centimetre** `width`/`depth`/
`height` ScannedItem shape that predates the heightmap; it no longer matches
this file -- see `tests/test_io.py::TestScannedItem` for its own fixture.

## Schema facts

- Units: **meters** everywhere in `Scene`/`Object`/`Container` JSON, and in
  `examples/scanned_item.json`.
- Coordinates: X = right, Y = **up**, Z = forward (right-handed, matches ARKit).
- Rotation: quaternion `[x, y, z, w]`, unit length. Absent/`null` -> identity
  `[0, 0, 0, 1]`.
- `dimensions = [x_extent, y_extent, z_extent]` -- local x/y/z *before*
  rotation. `y` is the vertical extent when unrotated.
- Placements accept either `position`/`rotation` or `target_position`/
  `target_rotation` keys (both spellings appear in the team docs).
