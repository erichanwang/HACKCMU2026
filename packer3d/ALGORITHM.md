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

## 8. Module map (for anyone editing the algorithm itself)

| file | responsibility |
|---|---|
| `models.py` | `Item`, `Container`, `Obstacle`, `Placement`, `PackResult` — data + validation + `from_scan`/`from_mesh` |
| `decoder.py` | Layer 1: extreme-point + grid-fallback constructive placement, vectorized feasibility checks |
| `search.py` | Layer 2: multi-start greedy + simulated annealing over (item order, orientation); `pack_naive`/`pack_optimized` entry points |
| `balance.py` | Layer 3: exact O(1) mass-swap balancing among identical-shaped items |
| `objective.py` | `ObjectiveWeights`, scoring used during search, `compute_metrics` for the final report |
| `verify.py` | independent from-scratch re-check of containment/overlap/mass/fragile/support — always run this |
| `bounds.py` | provable lower bounds, `gap_report`, `exhaustive_small` (brute force for <=7 items, sanity-checks the search) |
| `scenario.py` | JSON scenario loader (`load_scenario`) |
| `cli.py` | `python -m packer3d.cli scenario.json --time 6 --compare --gap --out result.json` |
| `visualize.py` | matplotlib debug render: `python -m packer3d.visualize result.json out.png optimized` |

See [README.md](README.md) for the algorithm's internal reasoning (why extreme points, why
simulated annealing, why mass-swap balancing), measured numbers, and known limitations.
