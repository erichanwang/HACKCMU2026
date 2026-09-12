# packer3d — Algorithm Documentation (HackCMU 2026)

This document describes the packing algorithm module of the project: what goes into it,
what comes out of it, and what every internal piece does. It is written for a teammate
integrating the lidar scanner (input side) or the Three.js frontend (output side)
without needing to read the source.

For a product-level pitch (numbers, known limitations, tuning), see [README.md](README.md).
This file is the API/data-contract reference.

---

## 1. What this module does

Given:
- a **container** (a suitcase, truck bed, or cargo capsule — box or cylinder shaped), and
- a **list of items** to pack (boxes, cylinders, or arbitrary scanned meshes),

it produces:
- a **placement** for every item that fits (position, orientation, dimensions), and
- a list of **items that did not fit**, with a reason, and
- **metrics**: volume utilisation, total mass, and centre-of-mass offset from a target.

Every output is **independently re-verified** (`verify()`) before being trusted — this
catches bugs in the search/decoder rather than silently returning a broken packing.

---

## 2. Coordinate system (shared contract with the frontend)

- Right-handed: **x = length, y = width, z = up**.
- Origin is the container's **minimum corner** (0, 0, 0).
- A box container spans `[0, L] x [0, W] x [0, H]` where `dims = (L, W, H)`.
- A cylindrical container is **vertical**: `dims = (2R, 2R, H)`, its circular cross-section
  is centered at `(R, R)` in x/y, and it spans `z in [0, H]`.
- Every placement's `position` is the **minimum corner** of that item's oriented bounding box
  (not its center — `center` is provided separately for convenience).

---

## 3. Input: building items

There are three ways to construct an `Item`, depending on what the lidar pipeline gives you.

### 3a. You already know it's a box or cylinder, with clean dimensions

```python
from packer3d import Item

Item.box("crate1", length=0.6, width=0.4, height=0.4, mass=12.0)
Item.cylinder("tank1", radius=0.2, height=0.75, mass=95.0, keep_upright=True)
```

| field | meaning |
|---|---|
| `id` | unique string, required |
| `mass` | kg, default 0 (unknown mass is fine — see CoM section) |
| `fragile` | if true, nothing may ever be placed on top of this item |
| `keep_upright` | if true, only orientations with this item's original "up" axis pointing to world z are allowed (forbids laying it on its side) |
| `allow_lay_down` | cylinders only; if false, only the standing (`axis="z"`) orientation is used |
| `priority` | higher priority items are dropped last when everything doesn't fit (default 1.0) |
| `compressibility_k` | loose volume / squeezed volume, default 1 (incompressible); normally set via `Item.compressed(k)`, which also squashes `dims` (height / k) to match |

### 3b. Lidar gives you `length, depth, height` + a shape label

```python
from packer3d import Item

Item.from_scan(id="obj_017", shape="box", length=0.30, depth=0.20, height=0.10, mass=1.2)
Item.from_scan(id="obj_018", shape="cylinder", length=0.10, depth=0.10, height=0.30)
Item.from_scan(id="obj_019", shape="oval", length=0.32, depth=0.22, height=0.40)  # -> irregular
```

- `shape` is case-insensitive; `"box"/"cuboid"/"cube"` and `"cylinder"/"cyl"/"can"/"bottle"/"tube"`
  are recognized. **Anything else** (`"oval"`, `"trapezoid"`, `"mannequin"`, unrecognized strings)
  falls back to **packing as its bounding box**, with `fragile=True` by default (its top isn't
  flat, so nothing should be stacked on it). Override with `fragile=False` if you know better.
- Cylinder radius is taken as `min(length, depth) / 2`.
- Mass is optional — omit it if the scanner can't weigh things (see §7, Centre of Mass).

### 3c. Lidar gives you a full mesh (point cloud triangulated, or a mesh export)

```python
from packer3d import Item

item = Item.from_mesh("mannequin_02", vertices, faces, mass=4.0)
# vertices: (N,3) array-like of xyz points
# faces: (M,3) array-like of triangle vertex indices (optional, but needed for volume + classification)
```

What this does automatically:
1. **Finds the tightest bounding box** by rotating the mesh about the z-axis in 1-degree steps
   and keeping the smallest footprint area. This avoids a bloated box when the scan wasn't
   axis-aligned. The chosen rotation is stored as `item.scan_yaw_deg` and is echoed in the
   output placement so the frontend can draw the *original mesh* at the correct rotation
   (rather than drawing a plain box).
2. **Measures true volume** from the closed triangle mesh (signed tetrahedron method). This
   feeds into utilisation metrics instead of the (larger) bounding-box volume.
3. **Classifies the shape**:
   - `mesh_volume / bbox_volume >= 0.92` → treated as a **box**
   - a roughly square footprint and `mesh_volume / bbox_volume ~ pi/4` → treated as a **cylinder**
   - anything else → **`"irregular"`** — packed as its bounding box, `fragile=True` by default.
4. Stores the original classification as `item.scan_shape` (`"box"`, `"cylinder"`, or `"irregular"`)
   so the frontend knows whether it's looking at an exact primitive or a bounding-box stand-in
   for something like a mannequin, an oval vase, or a trapezoidal wedge.

**What this does NOT do:** nest concave objects into each other (a bowl inside a bowl, a helmet
over a head shape). All collision/support is done against the object's bounding box. This is
always *safe* (no real overlap) but not always *tight* (wasted space around odd shapes). See
README.md §"How to make it stronger next" for the voxel-occupancy upgrade that would fix this.

### 3d. Building a whole scenario from JSON

Instead of constructing `Item`/`Container` in Python, you can write one JSON file:

```json
{
  "container": {"id": "truck", "shape": "box", "dims": [4.0, 2.0, 2.2], "gravity": true, "max_mass": 1500},
  "items": [
    {"id": "box1", "shape": "box", "dims": [0.6, 0.4, 0.4], "mass": 12, "count": 5},
    {"id": "scan1", "shape": "box", "length": 0.3, "depth": 0.2, "height": 0.1, "mass": 1.5}
  ],
  "optimizer": {"time_budget_s": 5, "seed": 0},
  "weights": {"unpacked": 10, "compact": 1, "com": 6, "height": 0.5}
}
```

```python
from packer3d import load_scenario, pack_optimized
container, items, config, weights = load_scenario("scenario.json")
result = pack_optimized(container, items, config, weights=weights)
```

`"count": n` on an item entry expands it into `id_1 .. id_n` copies — convenient when the
scanner reports a batch of identical objects. See `examples/dragon_resupply.json` and
`examples/suitcase.json` for full worked examples (a microgravity cargo capsule and a
gravity-packed overstuffed suitcase).

An item entry can also carry the server's scan-document fields (`SCAN_OUTPUT.md`) straight
through, in place of `fragile`/`keep_upright`/a squeezed size:

```json
{"id": "shirt", "shape": "box", "dims": [0.3, 0.2, 0.2], "rigidity": "soft",
 "keepUpright": false, "compressibility": 2.0}
```

`rigidity: "fragile"` sets `fragile=True`; `keepUpright` maps straight to `keep_upright`;
`compressibility: k` calls `Item.compressed(k)` on the loaded item, squashing its height to
`height / k` (`k` = loose volume / squeezed volume, so a folded t-shirt at `k=2` packs in half
the height). `rigidity` defaults to `"soft"`, so a bare `compressibility` with no `rigidity`
key is still trusted; `rigidity: "rigid"` ignores a stray `compressibility`. This applies to
every item form above (`dims`, `length`/`depth`/`height`, `radius`/`height`, or a heightmap —
see §8), not just boxes.

---

## 4. Defining the container

```python
from packer3d import Container, Obstacle

Container(
    id="dragon_capsule",
    dims=(3.2, 3.2, 1.15),        # (L, W, H); for a cylinder this must be (2R, 2R, H)
    shape="cylinder",              # "box" | "cylinder"
    gravity=False,                 # False = microgravity (any height allowed, no "floor" requirement)
    max_mass=3307,                 # kg; omit or math.inf for no limit
    min_support=0.7,                # fraction of an item's base that must rest on something flush (gravity only)
    obstacles=[Obstacle("hatch", position=(1.35, 1.35, 0.95), dims=(0.5, 0.5, 0.2))],
    com_target=None,                # None = auto (container center in x/y; z=0 gravity, z=H/2 microgravity)
    com_axis_weights=None,          # None = auto ((1,1,0.5) gravity, (1,1,0.1) microgravity) — lateral dominates
)
```

Obstacles are fixed solids (a hatch, a wheel arch) that items must avoid and that also act
as valid support surfaces (an item can rest on top of an obstacle).

---

## 5. Running the packer

```python
from packer3d import pack_optimized, pack_naive, OptimizerConfig, ObjectiveWeights, verify

result = pack_optimized(
    container, items,
    OptimizerConfig(time_budget_s=5, seed=0),   # more time = better packing, deterministic per seed
    weights=ObjectiveWeights(unpacked=10, compact=1, com=6, height=0.5),
)

errors = verify(result, items)   # ALWAYS check this before trusting/demoing a result
assert errors == []
```

- `pack_optimized` — the real algorithm (multi-start + simulated annealing + mass-swap
  balancing). Use this.
- `pack_naive` — a first-fit baseline (given order, first orientation, bottom-back-left
  position). Only useful as a "before" comparison for a demo.
- `OptimizerConfig.time_budget_s` — wall-clock seconds to spend searching. 3–6s is plenty
  for a demo-sized scenario (10–30 items). Set `time_budget_s=0, max_iterations=N` for a
  fully deterministic run (same seed + same N -> byte-identical output, useful for tests).
- `ObjectiveWeights` — trade-offs. Raise `com` for a launch-vehicle-style balance demo;
  raise `compact` if the story is "fit more stuff in a truck."

---

## 6. Output: the result object

`result` is a `PackResult` with `.placements`, `.unpacked`, `.metrics`, `.stats`, and
`.container`. Serialize it for the frontend with:

```python
json_text = result.to_json()          # or result.to_dict() for a plain dict
```

### 6a. Full JSON shape

```jsonc
{
  "strategy": "optimized",
  "container": {
    "id": "dragon_capsule", "shape": "cylinder", "dims": [3.2, 3.2, 1.15],
    "gravity": false, "max_mass": 3307.0, "min_support": 0.7,
    "obstacles": [{"id": "hatch", "position": [1.35, 1.35, 0.95], "dims": [0.5, 0.5, 0.2]}],
    "com_target": [1.6, 1.6, 0.575], "com_axis_weights": [1.0, 1.0, 0.1]
  },
  "placements": [
    {
      "item_id": "tank1", "shape": "cylinder",
      "position": [1.0, 1.0, 0.0],     // MIN corner of the oriented bounding box
      "dims": [0.4, 0.4, 0.75],         // oriented bounding box (dx, dy, dz)
      "center": [1.2, 1.2, 0.375],
      "orientation": "cyl_axis_z",      // see §6b
      "mass": 95.0, "fragile": false,
      "axis": "z", "radius": 0.2, "height": 0.75,          // cylinders only
      "scan_shape": "cylinder", "scan_yaw_deg": 0.0        // present only if built via from_scan/from_mesh
    }
  ],
  "unpacked": [
    {"id": "freezer", "reason": "would exceed the container mass limit"}
  ],
  "metrics": {
    "items_packed": 21, "items_unpacked": 1,
    "volume_utilization": 0.244,             // packed true volume / usable container volume
    "bbox_extent_utilization": 0.485,         // volume of the bounding box actually used / container volume
    "packed_volume": 2.25, "usable_volume": 9.20,
    "total_mass": 1361.0, "max_mass": 3307.0,
    "com": [1.59, 1.59, 0.29], "com_basis": "mass",   // or "volume_centroid" if all items are massless
    "com_target": [1.6, 1.6, 0.575],
    "com_lateral_offset": 0.017,               // meters, x/y distance from target — the headline CoM number
    "com_offset_3d": 0.29, "com_deviation": 0.079,   // axis-weighted normalized deviation (search objective term)
    "max_height": 0.75, "max_height_fraction": 0.65,
    "unpacked_priority_volume_fraction": 0.0,
    "objective": 0.959                          // lower is better; internal search score, mostly for debugging
  },
  "stats": {
    "multistart_runs": 24, "sa_iterations": 646, "sa_accepted": 154, "sa_improvements": 7,
    "balance_swaps": 2, "time_s": 5.0, "seed": 0
  }
}
```

### 6b. `orientation` values

- **Boxes**: one of `"xyz","xzy","yxz","yzx","zxy","zyx"` — an axis permutation. `"xzy"` means
  world-x is the item's original x, world-y is the item's original z, world-z is the item's
  original y (i.e., the item got tipped over). If you don't need to reason about which face is
  which, ignore this field and just draw a box of size `dims` at `position`.
- **Cylinders**: `"cyl_axis_x"`, `"cyl_axis_y"`, or `"cyl_axis_z"` — which world axis the
  cylinder's long axis points along. `axis` field repeats this as `"x"|"y"|"z"` for convenience.
  Use `radius`/`height`/`axis`/`center` to draw a real cylinder mesh (not the bounding box).

### 6c. Drawing scanned (non-primitive) items

If a placement has `"scan_shape": "irregular"`, it was built from `Item.from_scan`/`from_mesh`
and packed as a bounding box stand-in for something like a mannequin or an oval vase. To render
it faithfully:
1. Take the frontend's own copy of the original mesh for that item id (the packer does not
   store or return mesh geometry — only dimensions).
2. Rotate it by `scan_yaw_deg` about z (this is the rotation the packer *assumed* when computing
   the tight bounding box).
3. Translate it so its bounding box's min corner lands at `position`.

If `scan_shape` is `"box"` or `"cylinder"`, the object was classified as a true primitive and
you can just draw a box/cylinder of `dims`/`radius`/`height` — no original mesh needed.

### 6d. `unpacked` reasons (exact strings, for UI display)

| reason | meaning |
|---|---|
| `"larger than the container in every allowed orientation"` | doesn't physically fit no matter how it's rotated |
| `"would exceed the container mass limit"` | fits geometrically, but pushes total mass over `max_mass` |
| `"no feasible position (space, support or fragile constraints)"` | fits in principle, but no legal spot was found given what's already packed |

---

## 7. Centre of mass — how to interpret it, what it assumes

- If **any** item has nonzero mass, CoM is the true mass-weighted centroid (`com_basis: "mass"`).
- If **all** items are massless (e.g. the scanner can't weigh things and you didn't provide
  estimates), CoM falls back to the **volume centroid** (`com_basis: "volume_centroid"`) so you
  still get a geometric balance number, just not a physically weighted one.
- `com_target` defaults to the container's horizontal center; `com_lateral_offset` (meters) is
  the single number worth putting on a dashboard — "how far off-center is the load."
- This is a **first-order approximation** — no moments of inertia, no CoM envelope limits along
  an axis. Fine for a hackathon pitch; say "first-order CoM balance," not "flight-certified."

---

## 8. Connecting the LiDAR spike's real output (heightmap)

The scanner (`Spike/ScanView.swift`, documented in `SCAN_OUTPUT.md`) does **not** just hand
you `length/depth/height` + a shape label -- it hands you a bounding box **plus a heightmap**:
a 2D grid of the object's real surface height at each footprint cell, in centimetres.

```json
{ "id": "6F3A...", "width": 21.3, "depth": 12.1, "height": 8.4, "cellSize": 1.0,
  "heights": [[8.4, 8.4, 8.3, 0.0, 0.0, ...], [8.4, 8.4, 8.2, 0.0, 0.0, ...], ...] }
```

```python
from packer3d import Item
item = Item.from_scanned_heightmap(scan_json_dict, mass=1.2)
item.scan_shape    # "box" | "cylinder" | "irregular" -- classified from the heightmap
item.true_volume   # real volume integrated from the heightmap, not the bounding-box volume
```

What it does with the heightmap (instead of just discarding it into a plain box, which is
what `physics.io.object_from_scanned_item` currently does with this same payload):

* **Real volume**: sums `height * cellSize^2` over every cell, so utilisation metrics reflect
  the object's actual shape (an open shoe reports much less volume than its bounding box).
* **Classification**: footprint mostly filled and volume close to the full box -> `"box"`;
  roughly square footprint with a circular fill fraction (~ pi/4 of the box) -> `"cylinder"`;
  anything else (an L-bracket, an open shoe, a hole through the middle) -> `"irregular"`,
  packed as its bounding box with `fragile=True` by default (its top isn't flat/complete).
* Still **only 2.5D**: a single top-down view, so undercuts and overhangs hidden from the
  scanner (a mushroom shape) aren't captured -- same limitation the scanner itself documents.

A scan JSON can also be dropped straight into a scenario file's `"items"` list (anything with
a `"heights"` key is routed here automatically by `load_scenario`).

The server stores and returns this same object in **metres**, with a single `"dimensions":
[width, height, depth]` key instead of separate `width`/`depth`/`height` (`SCAN_OUTPUT.md`).
`Item.from_scanned_heightmap` detects that form automatically -- `"dimensions"` present and no
`"width"` key -- and reads it as metres instead of the spike's centimetres. The server document
also adds `rigidity`, `keepUpright`, and `compressibility`; `load_scenario` reads those the same
way for a heightmap item as for any other item form (see §3d).

## 9. Connecting to the `physics` validator / renderer (different coordinate convention)

The rest of the app (`physics/`, documented in `docs/PHYSICS.md` and `docs/INTEGRATION.md`)
uses a **different** coordinate convention than packer3d does internally:

| | packer3d (internal) | `physics` schema |
|---|---|---|
| up axis | z | **y** |
| item position | bounding box's **min corner** | object's **center** |
| orientation | axis-permutation string (`"xzy"`) / cylinder axis (`x`/`y`/`z`) | **quaternion** `(x,y,z,w)` |
| item dims order | (length, width, height) | (width, height, depth) |

`packer3d.physics_bridge` converts one to the other without packer3d importing anything
outside itself (it only emits plain dicts shaped like `physics.schema.Object`/`Container`,
plus the exact `{id, position, rotation}` placement list `physics.io.apply_placements`
expects):

```python
from packer3d import pack_optimized, to_physics_placements, physics_container_dict, physics_object_dict, verify

result = pack_optimized(container, items, config)
assert verify(result, items) == []

placements = to_physics_placements(container, result)   # -> [{"id","position","rotation"}, ...]
container_dict = physics_container_dict(container)       # -> physics.schema.Container(**container_dict)
object_dicts = [physics_object_dict(it) for it in items] # -> physics.schema.Object(**d) (dims/mass/constraints; no pose)

# then, on the physics side:
#   from physics.schema import Container, Object, Scene
#   from physics.io import validate
#   scene = Scene(Container(**container_dict), [Object(**d) for d in object_dicts])
#   result = validate(scene, placements)
```

**Correctness note (why this needed care, not just an axis relabel):** packer3d only tracks
*which* of an item's dimensions ends up along which world axis (enough to check axis-aligned
bounding boxes don't overlap) -- it never records which of the (always at least one, often
two) actual physical rotations achieving that face arrangement was used, because for a
box/cylinder that distinction is invisible to collision. The bridge picks one consistent,
always-proper (never mirrored) rotation for each of the 6 box orientations and 3 cylinder
axes, chosen so that "no reorientation" maps to the identity quaternion. This is verified in
`tests/test_physics_bridge.py`: every orientation is round-tripped through the *exact*
`quat_to_matrix` formula from `physics/geometry.py` (copied into the test, not imported, so
packer3d stays dependency-free) and checked to reproduce the same world-aligned extents and
position as packer3d's own placement, independent of the bridge's internal formula.

**Known limitation, not fixed here:** for a scanned **irregular** mesh (a mannequin, an open
shoe) that ambiguity is no longer invisible -- an asymmetric object rendered at "the other"
valid rotation can look upside-down or backwards even though its bounding box (and every
physics collision check) is identical. Boxes, cylinders, and anything symmetric under a
180-degree turn about its own axes are unaffected. Fixing this for real requires packer3d to
track actual chosen rotations for asymmetric items, not just bounding-box orientation --
noted here rather than silently working around it.

## 10. Hardening pass (112 tests total, up from 40)

A dedicated adversarial pass found and fixed four real bugs, plus closed several gaps that
were silent-wrong rather than crashing:

* **`verify()` used to crash** (`ZeroDivisionError`) on a placement with a zero-area footprint
  above the floor, and could silently pass NaN/inf-corrupted positions (comparisons against NaN
  are always `False`). Fixed: `verify()` now flags non-finite values, negative dims, and
  zero-area footprints as explicit violations instead of crashing or staying silent -- the one
  function whose entire job is "check even a hand-tampered result" now actually does that.
* **`OptimizerConfig(t_start=0.0)` used to crash** with a raw `ZeroDivisionError` deep in the
  annealing loop, on the very first call. `OptimizerConfig`/`DecoderParams` fields are now
  validated at construction (`time_budget_s`, `max_iterations`, `t_start`, `t_end`,
  `com_weight_grid` all raise a clear `ValueError` for non-finite, negative, or wrong-type
  values) instead of failing confusingly mid-search.
* **A cylindrical container used to bridge silently and wrongly** into the physics package's
  schema, which has no concept of a cylindrical container (`physics.schema.Container` is
  always an oriented box). `physics_container_dict`/`to_physics_placements` now raise
  `ValueError` for a non-box container rather than emitting a Scene whose validator would
  treat every corner of a much-larger bounding box as valid interior space.
* **The scenario JSON loader used to defeat the "irregular defaults to fragile + upright"
  smart default** by always forcing a concrete `True`/`False` for `fragile`/`keep_upright`
  before calling `Item.from_scan`/`from_scanned_heightmap`. Fixed: those fields are now passed
  through as `None` unless explicitly present in the JSON, matching the same-name Python API.
* **Irregular scans/meshes now default to `keep_upright=True`** (in addition to the existing
  `fragile=True`), across `from_scan`, `from_mesh`, and `from_scanned_heightmap`. This is a
  deliberate product decision, not just a bug fix: an unrecognized/asymmetric object (a
  mannequin, an open shoe) is never deliberately tipped onto its side by the solver, which is
  both more realistic for a demo and reduces (though does not eliminate -- see the yaw-sign
  note below) how often the physics-bridge rotation ambiguity in §9 is actually exercised.
  Override with an explicit `keep_upright=False` if an item can safely lie down.
* Every `from_scan`/`from_mesh`/`from_scanned_heightmap` call now validates its own input
  (missing required keys, ragged heightmap rows, non-finite or negative heightmap values,
  non-positive width/depth/height) with a clear `ValueError` instead of a raw `KeyError` or an
  opaque numpy error.
* The scenario JSON loader validates missing `container`/`dims`/item `id` keys, a shape-specific
  missing field (`dims` for a box, `radius` for a cylinder, `depth`/`height` for a lidar
  payload), a missing obstacle key, and rejects `count <= 0` (previously: silently zero items,
  no error -- a likely typo that would otherwise vanish without a trace).

**Verified, not just fixed:** an independent overlap cross-check re-derives each bridged
placement's world-space OBB corners from scratch (not reusing `physics_bridge`'s own formula)
and confirms no two overlap in the *physics* coordinate frame either -- a second, independently
coded check of the same property `verify()` already checks in packer3d's own frame. Also
covered: numerical extremes (1e-4 m and 1e5 m scale items in the same run), 1000-item
performance, three-way fractional-priority competition, boundary equality on `min_support`
(exactly-required support must pass, not fail), balancing verified to never worsen CoM across
8 randomized trials while leaving the item-position set unchanged, and orientation
de-duplication for perfect cubes and square-footprint slabs.

**Still an open, documented limitation (not fixed here, see §9):** the rotation-chirality
ambiguity for a genuinely asymmetric mesh remains -- `keep_upright=True` narrows it (removes
the "on its side" ambiguity) but does not remove the remaining left/right yaw-sign ambiguity
for an asymmetric footprint. Fixing that fully needs packer3d to track actual chosen
rotations, not just bounding-box orientation, which is a larger change than a hardening pass.

## 11. Module map (for anyone editing the algorithm itself)

| file | responsibility |
|---|---|
| `models.py` | `Item`, `Container`, `Obstacle`, `Placement`, `PackResult` — data + validation + `from_scan`/`from_mesh` |
| `decoder.py` | Layer 1: extreme-point + grid-fallback constructive placement, vectorized feasibility checks |
| `search.py` | Layer 2: multi-start greedy + simulated annealing over (item order, orientation); `pack_naive`/`pack_optimized` entry points |
| `balance.py` | Layer 3: exact O(1) mass-swap balancing among identical-shaped items |
| `objective.py` | `ObjectiveWeights`, scoring used during search, `compute_metrics` for the final report |
| `verify.py` | independent from-scratch re-check of containment/overlap/mass/fragile/support — always run this |
| `bounds.py` | provable lower bounds, `gap_report`, `exhaustive_small` (brute force for <=7 items, sanity-checks the search) |
| `scenario.py` | JSON scenario loader (`load_scenario`) -- also routes heightmap-scan item dicts to `Item.from_scanned_heightmap` |
| `physics_bridge.py` | converts packer3d output into the physics package's schema (center position, quaternion rotation, Y-up) |
| `cli.py` | `python -m packer3d.cli scenario.json --time 6 --compare --gap --out result.json` |
| `visualize.py` | matplotlib debug render: `python -m packer3d.visualize result.json out.png optimized` |

See [README.md](README.md) for the algorithm's internal reasoning (why extreme points, why
simulated annealing, why mass-swap balancing), measured numbers, and known limitations.
