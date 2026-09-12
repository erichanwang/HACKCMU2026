# CLAUDE.md

## What this project is

An iOS app built for HackCMU. A separate **solver** computes a suitcase packing plan and
emits it as **JSON**. This app consumes that JSON and shows the user where each item goes:

1. **AR overlay** — items rendered in place inside the real, physical suitcase, so the user
   can look into the open bag and see the next item's slot.
2. **2D fallback view** — a flat, schematic rendering of the same plan (layer/slice or
   top-down diagram). This is not a second-class path: it must work with no AR session,
   on failed plane/bag detection, and for users who decline camera access.

The app does **not** solve the packing problem. Treat the plan as an input. If a plan looks
wrong, surface it rather than silently repairing geometry.

## Non-negotiable conventions

These hold in **every** layer — parsing, solver fixtures, AR code, 2D code, tests, and any
debug output. Do not introduce a local exception "just for rendering."

### Units

- **All lengths are meters.** All positions, all sizes, everywhere.
- No centimeters, no inches, no "display units" in the model layer. Convert to cm/in only
  at the moment of formatting a string for the user, never in stored or passed values.
- Prefer `Float` (RealityKit-native) in geometry types; be consistent so no implicit
  `Double`↔`Float` widening hides a unit bug.

### Coordinate frame

Origin is the **inner-front-left floor corner of the bag interior** — the interior cavity,
not the outer shell, and not the lid.

- **X = width**, increasing to the right
- **Y = up**, increasing away from the floor
- **Z = depth**, increasing toward the viewer/front
- **Right-handed**

So the bag interior occupies `[0, width] × [0, height] × [0, depth]`, and `(0,0,0)` is a
corner of that box. Every coordinate in the plan is in this bag-local frame — never in ARKit
world space. World placement happens exactly once, at the anchor that maps bag-local space
onto the detected physical bag.

**Unresolved: handedness vs. the named origin corner.** These two requirements conflict as
literally stated. With all extents positive from the origin corner, `{X right, Y up,
Z increasing away from the viewer}` is **left**-handed — right-handedness with X right and
Y up forces +Z *toward* the viewer, which would put the bag interior at negative Z if the
origin really is the front corner. Pick one:

- **(a)** Read the origin corner as the inner-**back**-left floor corner, so +Z runs toward
  the viewer. Right-handed, RealityKit-native, no sign flips anywhere. One word changes.
- **(b)** Keep "front-left" literally and accept a left-handed plan frame, which then needs
  a Z flip exactly once, at the anchor that maps bag space into RealityKit.

Until this is settled, the model layer commits to neither: it stores the interior as
`[0, dimensions]` on every axis and does no handedness conversion. Overlap, containment, and
the min-corner rule are all unaffected by the choice — only the single bag→world anchor
transform is. **(a)** is recommended.

### Position means MIN-CORNER, not center

A placement's `position` is the **minimum corner of its axis-aligned bounding box** — the
corner nearest the origin on all three axes. It is **not** the centroid.

RealityKit (and most 3D APIs) position a box entity by its **center**. Therefore:

```
center = position + size / 2
```

**Every renderer must apply the `size / 2` offset.** This is the single most likely bug in
this codebase: forgetting it puts every item off by half its own dimensions, which looks
"almost right" — items sink into the floor and hug the left/front walls — and is easy to
mistake for anchor drift or tracking error.

Consequences to keep straight:

- An item occupies `[position, position + size]` on each axis.
- A placement is inside the bag iff `position >= 0` and `position + size <= interior` on all
  three axes.
- Overlap tests, containment checks, and layer/slice bucketing in the 2D view all use the
  min-corner/extent form directly — only the *rendering* step converts to a center.
- Do this conversion in **one shared helper**, not inline at each call site, so there is a
  single place to be right. Same rule in 2D: a rect's origin is the min corner of its
  projected face.

### Rotation / orientation

If a placement carries an orientation, `size` is the item's extent **after** rotation
(i.e. the AABB the solver reserved), so `position + size` stays the occupied box and the
`size / 2` rule is unchanged. Any visual mesh rotation is applied inside that box about its
center. Do not rotate the box and then re-derive `position`.

## Plan JSON

The plan is the contract between solver and app. The models live in
`Sources/PackingPlan/PackingPlan.swift`; a hand-authored reference plan is at
`Sources/PackingPlan/Resources/plan.json` — the real demo suitcase interior,
0.4064 × 0.1524 × 0.6096 m (16 × 6 × 24 in), with six items in three zones. The `base` and
`upper` zones overlap in Y, deliberately: the base layer is not uniformly thick (the jeans are
3.5 cm, the sweater roll 7 cm), so there is no single shelf height that both base items fit
under and an upper item can rest on. Nothing checks zone disjointness; placement boxes are
what must not overlap, and they do not.

```json
{
  "version": 1,
  "units": "meters",
  "container": {
    "id": "demo-carry-on",
    "label": "Demo carry-on (16 x 6 x 24 in)",
    "dimensions": { "x": 0.4064, "y": 0.1524, "z": 0.6096 },
    "zones": [
      { "id": "end-well", "label": "End well",
        "origin": { "x": 0.0000, "y": 0.0000, "z": 0.0000 },
        "size":   { "x": 0.4064, "y": 0.1524, "z": 0.1700 } }
    ]
  },
  "placements": [
    {
      "step": 1,
      "itemId": "shoes-pair",
      "label": "Running shoes",
      "zone": "end-well",
      "position": { "x": 0.0000, "y": 0.0000, "z": 0.0000 },
      "size":     { "x": 0.3000, "y": 0.1150, "z": 0.1500 },
      "rotation": "XYZ",
      "note": "Soles down, side by side, pushed flush into the end corner."
    }
  ]
}
```

- Vectors are keyed `{x, y, z}` objects, never bare arrays. Hand-authoring a plan is
  error-prone enough without having to remember positional axis order.
- `units` must be `"meters"`; decoding fails otherwise, so a centimetre plan cannot quietly
  render a suitcase 100× too big.
- `container.dimensions` is the **interior** cavity. Valid placements live in `[0, dimensions]`.
- `zones` are named sub-volumes (layers, pockets). `origin` is a zone's min corner, same
  convention as a placement's `position`. A placement's `zone` must name one that exists.
- `position` is the min-corner. `size` is the full extent in **bag axes**, already accounting
  for `rotation`.
- `rotation` is one of `XYZ XZY YXZ YZX ZXY ZYX`, read as the item-local axes assigned to bag
  X, Y, Z in that order — `XYZ` is identity. It orients the item's mesh and label; it does
  **not** re-permute `size`. Three of the six are odd permutations (reflections), so
  `AxisRotation.basisColumns` negates one column to keep a proper rotation — visually
  identical for a symmetric box.
- `step` is 1-based and must form `1...n` with no gaps or repeats; the loader enforces this.
- `note` is one short presentation-only line. Never parse it.
- `nestedIn` is optional and describes an item the solver deliberately placed **inside
  another item's scanned cavity** — socks in a shoe, a charger in the dip of a dopp kit.
  See below; absent or `null` means not nested, which is the common case.

### Nested placements

```json
"nestedIn": { "itemId": "dopp-kit",
              "cavity": { "position": { "x": 0.12, "y": 0.02, "z": 0.30 },
                          "size":     { "x": 0.08, "y": 0.04, "z": 0.10 } } }
```

Two items' boxes may legitimately intersect when one sits in the other's cavity, so a
consumer that treats every item as one solid box reports a correct plan as an overlap.
`nestedIn` names the host and the **specific cavity cell**, in bag frame, min corner plus
full extent like every other box here. A host can have several cavity cells and "may these
two overlap" has a different answer in each, which is why the cell travels with the field
instead of just the host's id.

The field is an **assertion the solver has already checked** against its own solid
decomposition (`packer3d`'s `verify()` is the authority; `server/app_plan.py` copies it
verbatim and infers nothing — deriving nesting from "these boxes intersect" would relabel
any overlap the validator missed as legitimate and hide it).

Consumers trust it anyway, but verify the cell rather than the pair:

- intersection between a nested item and its host is permitted **only inside `cavity`**.
  Overlap anywhere outside it is still an overlap and is still reported. That is the line
  between a feature and a suppressed collision.
- the host's cavity counts as support for the nested item, so it does not read as floating.
- absent, `null`, an `itemId` not in the plan, a self-reference and a cycle are all treated
  as not nested. A dangling `itemId` additionally raises its own geometry issue — silently
  ignoring it would hide a producer bug.
- a host with no cavity cell is never representable: the producer emits `null` rather than
  a `nestedIn` the consumer cannot check.

### Loading vs validating

`PlanLoader` does **structural** validation only: decoding, `units`, unique item IDs, a
contiguous step sequence. Geometry is checked separately by `PackingPlan.geometryIssues()`,
which returns `[GeometryIssue]` rather than throwing, so a questionable plan can be opened
and shown to the user instead of failing to load. Call `validateGeometry()` where geometry
must be a hard requirement. Either way: report, never silently repair.

## Platform & tech

- **Target: iOS 17** (minimum deployment). Avoid iOS 18+ only APIs; if one is genuinely
  needed, gate it with `#available` and keep the iOS 17 path working.
- **SwiftUI** for all UI. No storyboards, no UIKit view controllers except where an AR
  view must be bridged (`UIViewRepresentable`).
- **ARKit** for world/plane tracking and anchoring the bag frame.
- **RealityKit** for rendering the overlay geometry.
- Swift Concurrency (`async`/`await`, `@MainActor`) over completion handlers or Combine.

## Working notes for agents

- **Verify the `size / 2` offset first** when anything looks misplaced in AR or 2D. Check it
  before touching anchoring, scale, or tracking code.
- Keep bag-local geometry pure and testable: the `PackingPlan` module deliberately imports
  neither ARKit nor RealityKit, so plan math runs under `swift test` with no session and no
  camera. Keep it that way — put rendering code in the app target, not in this module.
- AR cannot be verified in the simulator (no camera/world tracking). Use the 2D view and
  unit tests for geometry correctness; reserve device runs for anchoring and tracking.
- When adding a debug/inspector overlay, print min-corner *and* computed center side by
  side — it makes the offset bug visible immediately.
- Don't change the coordinate convention to make a rendering path easier. Change the
  rendering path.
