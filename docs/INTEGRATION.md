# Integration guide (iOS scan → JSON → validate → solver loop → renderer)

Teammate-facing walkthrough for wiring the physics layer into the rest of the
app. For the precise formulas/tables behind any of this, see
**`docs/PHYSICS.md`** (cross-linked below by section). This doc only covers
`physics/io.py`, `physics/__main__.py`, and `physics/incremental.py` — the
integration boundary; nothing here re-implements physics.

```
   iOS LiDAR scan                  packing solver                  renderer
  ┌────────────────┐   cm→m    ┌──────────────────┐   result   ┌─────────────┐
  │  ScannedItem   ├──────────►│  Object (no pose) │            │  contact_   │
  │  (JSON, cm)    │           │                   │            │  polygon,   │
  └────────────────┘           │  PlacementValidator│◄──queries─┤  axis,      │
   or BoxFit (m, yaw) ────────►│  .try_place/.place │           │  penetrating│
                                │  (inner loop, O(k))│           │  _vertices  │
                                └─────────┬──────────┘           └──────▲──────┘
                                          │ .to_scene()                 │
                                          ▼                             │
                                validate_layout(scene)  ───────────────►┘
                                  (one authoritative pass)
```

## 1. Getting an `Object` from a scan

Two scanner shapes exist; `physics/io.py` adapts both. See PHYSICS.md §11 for
the exact formulas.

**`ScannedItem`** (no pose yet — just dimensions, in **centimetres**):

```json
{ "id": "C5C90A5F-...", "width": 29.0, "depth": 12.0, "height": 11.0 }
```

```python
from physics.io import object_from_scanned_item
obj = object_from_scanned_item(item)   # cm -> m; position/rotation default to origin/identity
```

This `Object` is good enough for the **solver** to reason about (it only
needs dimensions to try placements) but is **not** a meaningful input to
`validate_layout`/`validate` until it has a real pose — the scanner never
gives you one.

**`BoxFit`** (already posed, **metres**, yaw-only):

```python
from physics.io import object_from_box_fit
obj = object_from_box_fit("shoe", width_m, depth_m, height_m, center=(x,y,z), axis=(ax, 0, az))
```

`axis` is the world unit vector of the box's width edge; the adapter derives
the yaw quaternion as `θ = atan2(-az, ax)`. Verified numerically in
`tests/test_io.py`. See PHYSICS.md §11 for the derivation.

## 2. Scene JSON shape

`scene_to_dict`/`scene_from_dict` round-trip a whole `Scene`. Units are
**meters** everywhere in this JSON (the cm→m conversion above only applies to
the raw `ScannedItem` scanner format, never to a `Scene`/`Object` dict).

```json
{
  "container": {
    "id": "carry_on",
    "dimensions": [0.56, 0.23, 0.36],
    "position": [0.0, 0.115, 0.0],
    "rotation": [0.0, 0.0, 0.0, 1.0]
  },
  "objects": [
    {
      "id": "laptop",
      "dimensions": [0.3, 0.02, 0.21],
      "position": [-0.125, 0.01, -0.07],
      "rotation": [0.0, 0.0, 0.0, 1.0],
      "mass_kg": 1.3,
      "constraints": {
        "fragile": true, "keep_upright": false,
        "cannot_support_weight": true, "heavy": false,
        "orientation_lock": "flat_only"
      },
      "rigidity": "rigid",
      "compressibility_k": 1.0
    }
  ]
}
```

Full working example: `examples/scene_carry_on.json` (7 objects, all
constraint fields exercised). Coordinates: X=right, Y=**up**, Z=forward
(matches ARKit). `dimensions = [x_extent, y_extent, z_extent]`, measured
before rotation — a `ScannedItem`'s `height` always lands on `dims[1]`
regardless of how the object ends up rotated once placed.

## 3. Feeding the solver's placements back in

The solver only needs to report `{id, position, rotation}` per object it
placed — **or** the equivalent `target_position`/`target_rotation` spelling
(both are accepted, since both appear across the team's docs):

```json
{ "placements": [
  { "id": "shoe", "target_position": [-0.13, 0.055, 0.075], "target_rotation": [0,0,0,1] }
]}
```

(A bare list `[{...}, ...]` works too — the CLI and `apply_placements` both
accept either shape.) Example: `examples/placements_collision.json`.

```python
from physics.io import validate
result = validate(scene, placements)   # apply_placements, then validate_layout
```

`validate_layout(apply_placements(scene, placements))` is exactly what
`validate` does, plus turning an unknown-id `ValueError` into a
`MALFORMED_GEOMETRY` violation instead of raising. Prefer `validate` over
composing the two yourself unless you specifically want the raise-on-unknown
behavior.

## 4. CLI

```sh
python3 -m physics validate examples/scene_carry_on.json --pretty
python3 -m physics validate examples/scene_carry_on.json --placements examples/placements_collision.json
python3 -m physics scan-to-object examples/scanned_item.json
python3 -m physics example   # prints scene_carry_on.json's shape, generated fresh
```

Exit codes: `0` = valid scene, `1` = ran fine but has violations, `2` =
couldn't read/parse the input file at all (distinct failure mode from "found
violations" — check this before treating output as a real result).

## 5. The solver's inner loop: `PlacementValidator`

Don't call `validate_layout` on every candidate placement while the solver is
searching — it's O(k²) per call. Use `physics.incremental.PlacementValidator`
instead, which is O(k) per query:

```python
from physics.incremental import PlacementValidator

pv = PlacementValidator(container)
for obj in objects_in_solver_order:
    result = pv.try_place(obj)          # cheap: check obj against what's committed so far
    if result["valid"]:
        pv.place(obj)                   # commit it
    else:
        ... try a different pose ...

scene = pv.to_scene()
final = validate_layout(scene)          # <-- always do this once, at the end
```

`try_place`/`place` return the same `{"valid","score","violations","warnings"}`
shape `validate_layout` uses (no `metrics` key), scoped to just the one
object being evaluated. **It is not a full substitute for `validate_layout`**
— it deliberately doesn't re-check already-committed objects against each
other (that's the whole point: it was already checked when each one was
placed), and it runs an older, simpler approximation of the support/
orientation logic than the current `support.py`/`constraints.py` (exact
convex-hull footprints, transitive load, chain instability — none of that is
replicated in the incremental path). See PHYSICS.md §11 for the exact list of
divergences.

**Rule of thumb**: `PlacementValidator` for "can I try this?" during search;
one `validate_layout(pv.to_scene())` call when the solver commits to a final
layout, before it's shown to the user or sent to the renderer.

## 6. Renderer-relevant fields

Every `validate_layout` violation/warning is meant to be directly renderable
— see PHYSICS.md §10's field table for the exhaustive list. The highlights:

| Need to render... | Look at |
|---|---|
| Which objects are overlapping, and where | `OBJECT_COLLISION.contact_point`, `.axis` |
| Which wall an object is poking through, and its corners | `CONTAINER_PENETRATION.violated_walls`, `.per_wall_depth_m`, `.penetrating_vertices` |
| A highlighted "unstable" object and its support base | `UNSTABLE_STACK.contact_polygon` (`[[x,z],...]`), `.center_of_mass_projection` |
| Overall packing quality / solver objective | `metrics.fill_ratio`, `metrics.com_offset_m`, `metrics.per_object[id]` |

`result_to_json(result)` handles the numpy scalars/arrays that leak into a
few of these fields (e.g. `support_ratio`) — always serialize through it
rather than a bare `json.dumps(result)`.
