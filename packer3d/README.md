# packer3d — 3D packing of boxes & cylinders with centre-of-mass optimisation

Packs rectangular and cylindrical items into a box or cylindrical container (suitcase, truck,
cargo capsule) while respecting mass limits, gravity/support, fragile items, orientation
rules and fixed obstacles — and optimises **both** space utilisation **and** centre of mass.

Every solution is **feasible by construction** and independently re-checked by `verify()`.

```bash
python3 -m venv .venv && .venv/bin/pip install numpy pytest matplotlib   # numpy is the only hard dependency
source .venv/bin/activate
PYTHONPATH=. python -m packer3d.cli examples/dragon_resupply.json --time 6 --compare --gap --out result.json
PYTHONPATH=. python -m packer3d.visualize result.json out.png optimized   # debug render (matplotlib)
PYTHONPATH=. python -m pytest tests -q                                   # 42 edge-case tests
```

Measured on this machine (6 s budget, seed 0):

| scenario | naive (first-fit) | optimised |
|---|---|---|
| Cargo-Dragon-style capsule, 22 items, microgravity, hatch obstacle | 22/22, CoM **12.7 cm** off-axis, stack 65 % of H | 22/22, CoM **1.8 cm** off-axis, stack 65 % of H, 0 % from the volume bound |
| Overstuffed suitcase, 18 items, gravity, 23 kg limit | 12/18 packed, 59 % util, CoM 3.2 cm off | **13–15/18** packed (seed-dependent), **66–72 %** util, CoM 1.4–5.9 cm off |

---

## Python API

```python
from packer3d import Container, Item, Obstacle, OptimizerConfig, ObjectiveWeights, pack_optimized, pack_naive, verify

c = Container("dragon", dims=(3.2, 3.2, 1.15), shape="cylinder", gravity=False, max_mass=3307,
              obstacles=[Obstacle("hatch", (1.35, 1.35, 0.95), (0.5, 0.5, 0.2))])
items = [
    Item.box("CTB1", 0.50, 0.42, 0.25, mass=27),
    Item.box("freezer", 0.9, 0.6, 0.7, mass=210, fragile=True, keep_upright=True, priority=4),
    Item.cylinder("tank", radius=0.2, height=0.75, mass=95, keep_upright=True),
]
result = pack_optimized(c, items, OptimizerConfig(time_budget_s=5, seed=0))
assert verify(result, items) == []           # independent geometric re-check
print(result.metrics["com_lateral_offset"], result.unpacked)
open("result.json", "w").write(result.to_json())   # -> feed to the Three.js frontend
```

### Plugging in the lidar output

The scanner gives `length, depth, height` and a shape per object. That maps 1:1 onto `Item.from_scan`:

```python
scans = [{"id": "obj1", "shape": "box", "length": 0.30, "depth": 0.20, "height": 0.10},
         {"id": "obj2", "shape": "cylinder", "length": 0.10, "depth": 0.10, "height": 0.30}]
items = [Item.from_scan(**s) for s in scans]          # cylinder radius = min(length, depth) / 2
result = pack_optimized(Container("suitcase", (0.75, 0.5, 0.28)), items)
```

The same keys work inside a scenario JSON (`"items": [{"id", "shape", "length", "depth", "height"}]`).
The scanner cannot give mass: leave it 0 (the CoM then uses the volume centroid) or add a weighed
`"mass"` per object to get real balancing.

**Full meshes** (what the lidar pipeline actually produces) go through `Item.from_mesh`:

```python
item = Item.from_mesh("mannequin", vertices, faces, mass=4.0)   # vertices (N,3), triangle faces (M,3)
item.scan_shape, item.scan_yaw_deg, item.volume                   # 'irregular', 27.0, 0.031
```

* the mesh is rotated about z to find the **tightest footprint** (a crate scanned at 30° is not
  packed in a 30°-bloated box); the yaw is exported per placement as `scan_yaw_deg` so the
  frontend draws the real mesh at the right angle;
* the closed-mesh volume classifies the object: volume/bbox ≈ 1 → **box**, ≈ π/4 with a square
  footprint → **cylinder**, anything else (oval, trapezoid, mannequin) → **irregular**;
* irregular objects are packed as their bounding box — always safe, never tight — and default to
  `fragile=True` because a non-flat top can't carry a load. Pass `fragile=False` to override;
* utilisation metrics use the measured mesh volume, not the box.

What a bounding box cannot do is nest objects into each other's concavities (a bowl in a bowl).
That needs voxel-occupancy collision instead of box collision — see "How to make it stronger".

Scenarios can also be loaded from JSON (`load_scenario(path)`, see `examples/`); an item entry
with `"count": n` expands into `id_1 .. id_n` — convenient for lidar-scanned batches.

### Output JSON contract (what the frontend consumes)

```jsonc
{
  "strategy": "optimized",
  "container": {"id": "...", "shape": "box|cylinder", "dims": [L, W, H], "gravity": true,
                "obstacles": [{"id", "position", "dims"}], "com_target": [x,y,z]},
  "placements": [{
    "item_id": "tank", "shape": "cylinder",
    "position": [x, y, z],          // MIN corner of the oriented bounding box
    "dims": [dx, dy, dz],           // oriented bounding box
    "center": [cx, cy, cz],
    "orientation": "cyl_axis_x",    // boxes: axis permutation "xyz","xzy",... (world axis <- item axis)
    "axis": "x",                    // cylinders only: world axis of the cylinder axis
    "radius": 0.2, "height": 0.75,  // cylinders only -> draw a real cylinder
    "mass": 95, "fragile": false
  }],
  "unpacked": [{"id": "...", "reason": "would exceed the container mass limit"}],
  "metrics": {"volume_utilization": 0.244, "com": [..], "com_lateral_offset": 0.018,
              "max_height_fraction": 0.65, "total_mass": 1361, "objective": 0.946, ...},
  "stats": {"sa_iterations": 1333, "balance_swaps": 2, "time_s": 6.0, ...}
}
```

`--compare` writes `{"naive": {...}, "optimized": {...}}` instead; `visualize` accepts both layouts.

Coordinates: right-handed, x = length, y = width, z = up, origin at the container's min corner.
Cylindrical containers are vertical, dims = `(2R, 2R, H)`, axis at `(R, R)`.

---

## How the algorithm works (three layers)

### 1. Constructive decoder — Extreme Points + best-fit scoring (`decoder.py`)
Given an item *sequence* and per-item *orientation choices*, items are placed one at a time:

* **Candidate positions = extreme points** (Crainic, Perboli & Tadei 2008). When a box is placed,
  its three "outer" corners are projected back along the other axes until they hit another solid
  or a wall (for cylindrical containers: the curved wall at that height). Those projected points
  are where the next box can sit flush — O(#items) candidates instead of a 3D grid.
* **Fallback = coordinate grid** (union of every solid's boundary coordinates + an even grid, plus
  the tightest positions against the curved wall for each row in a cylindrical container).
  Only used when no extreme point works. Under gravity only z-levels that are the floor or a
  solid's top are ever generated, since nothing else can support an item.
* **Vectorised feasibility**: one item is tested against *all* candidates in a handful of numpy
  ops — containment (box or circle: all four footprint corners inside), overlap, fragile-below,
  fragile-above (a fragile item may not be slid under an existing one), gravity support ratio.
* **Placement score** (lower = better):
  `w_z·z/H + w_y·y/W + w_x·x/L − w_contact·(touching face area / item surface) + w_com·CoM_dev_after`
  i.e. bottom-back-left preference, a reward for faces flush against walls/neighbours
  (kills slivers of dead space), and a look-ahead on where the centre of mass would land.
  Candidates are evaluated in chunks sorted by the cheap position term and the loop stops as
  soon as the remaining lower bound can't beat the best score (branch-and-bound).

### 2. Search — multi-start + simulated annealing (`search.py`)
* **Multi-start**: 6 sort orders (volume, mass, longest edge, footprint, thinnest, density —
  always priority-first) × 4 CoM weightings = 24 greedy packs with every orientation tried;
  best objective wins.
* **Simulated annealing** over the genome `(sequence, orientation per item)`, decoded by layer 1,
  so every candidate is feasible. Moves: swap, insert, reverse-segment, re-orient, and a
  **targeted repair move** that pushes an item that didn't fit to the front of the sequence
  (long/awkward items only fit while the floor is empty — generic mutation finds that by luck,
  this finds it on purpose).
* **Objective** (all terms ~[0,1], `ObjectiveWeights`):
  `10·unpacked_priority_volume + 1·used_space_from_origin + 6·CoM_deviation + 0.5·stack_height(gravity only)`

### 3. Mass-swap balancing — exact, geometry-preserving (`balance.py`)
After search, items with **identical oriented bounding boxes and identical fragile flag** swap
*positions* (not orientations). Geometry doesn't change at all, so every constraint still holds
without re-checking, but the mass distribution does: the moment update is
`Δ = (m_i − m_j)·(c_j − c_i)`, evaluated in O(1) (vectorised over each group). Greedy
best-improvement until no swap helps. On real cargo (many identical CTBs / tanks / crates)
this alone moves the CoM several cm.

### Centre-of-mass model — be honest about it in the pitch
We minimise the axis-weighted distance between the packed CoM and a target
(default: container centre in x/y; z = 0 under gravity, z = H/2 in microgravity; z is weighted
0.5 / 0.1 so **lateral** offset dominates). Real launch-vehicle balance also involves moments
of inertia and CoM *envelope* limits along the axis; this is the standard first-order
approximation — say "first-order CoM balance", not "flight-certified mass properties".

---

## Edge cases handled (and tested in `tests/test_edge_cases.py`)

| Case | Behaviour |
|---|---|
| empty item list | valid empty result, utilisation 0 |
| dims ≤ 0, NaN/inf, negative mass, priority ≤ 0, duplicate ids, bad container | `ValueError` with a message |
| zero-thickness "sheets" | rejected — give them a real thickness |
| item larger than container in every orientation | unpacked, reason says so |
| item fits only after rotation | rotation found automatically |
| `keep_upright` removes the only fitting rotation | correctly unpacked |
| exact fit (item = container) / exact tiling (8×5³ in 10³) | utilisation 1.0, no overlap (epsilon-safe) |
| one item too many | unpacked, never squeezed in overlapping |
| mass limit | never exceeded; low-priority items dropped first (priority sorts first) |
| `max_mass = 0` | only massless items pack |
| fragile item | nothing ever rests on its top face, and it is never slid under something (both modes) |
| scanned mesh / odd shape (oval, trapezoid, mannequin) | tight-yaw bounding box, classified box/cylinder/irregular, irregular = nothing stacked on top, mesh volume in metrics |
| gravity | base ≥ `min_support` supported by solids whose top is flush; floating forbidden |
| microgravity | any height allowed, still no overlap |
| obstacles | avoided, act as support and generate extreme points; an obstacle covering the whole floor is fine |
| cylinders | lie down when needed; `keep_upright` / `allow_lay_down` respected; true πr²h volume in metrics |
| cylindrical container | all four footprint corners inside the circle; too-wide planks rejected; seeds inside the inscribed square |
| all items massless | CoM falls back to the volume centroid (no divide-by-zero) |
| identical items | orientations de-duplicated; balancing swaps among them |
| 300 tiny items | ~0.5 s per naive decode, still verifies clean |
| determinism | same `seed` + `time_budget_s=0` + `max_iterations=N` → identical output (time-based budgets vary with CPU) |
| floating point | all comparisons use `EPS=1e-6`; positions rounded to 1e-9 to stop drift |

`verify(result, items)` re-checks containment, pairwise overlap, obstacles, mass, fragile,
support and legal orientation from scratch — call it before every demo.

### Known simplifications (say these out loud if asked)
* Cylinders pack as their bounding box (no hexagonal nesting). Exact for boxes, conservative for
  cylinders. Extension: true cylinder–cylinder / cylinder–box collision → tighter packs.
* Only 90° rotations. This is also what real loading software does (humans have to load it).
* A cylinder lying on its side under gravity is treated as stable (no rolling model).
* Support = area of solids whose top face is *exactly* flush with the item's base.
* The compactness term pulls items towards the origin; the CoM term pulls them to the centre.
  For a launch vehicle raise `ObjectiveWeights.com` (or lower `compact`).

## Is it optimal? How to know
3D bin packing is NP-hard, so nothing certifies a global optimum on realistic instances. What you
*can* do — and what the code gives you:

```python
from packer3d import gap_report, exhaustive_small
gap_report(result, items)   # distance to provable bounds (also: cli --gap)
```
* **Provable lower bounds** (`bounds.lower_bounds`): items can't overlap, so utilisation ≤ Σvolume/usable;
  if the mass limit is exceeded at least *k* items must go; items that fit in no orientation must go.
  If `items_unpacked == lower_bound` the packing is **optimal on count**.
  Dragon example: 22/22 packed, utilisation 0.0 % from the bound → done.
* **Gap** for tight instances: the suitcase reports 66–72 % utilisation vs a 100 % volume-only bound.
  That bound ignores gravity/fragile/upright, so the true optimum is somewhere in between —
  quote it as "within ~30 % of a bound that ignores physics".
* **Exhaustive check on small instances** (`exhaustive_small`, n ≤ 7): enumerate every sequence
  through the same decoder — the standard sanity check for a metaheuristic (`tests/` does this on
  a 4-item instance).
* **Variance across seeds** on over-full instances is the honest weakness (13–15 of 18 packed
  depending on seed at 3–6 s). More time helps.

## Tuning knobs

* `OptimizerConfig(time_budget_s, max_iterations, seed, t_start, t_end, balance, com_weight_grid)` — more time ≈ better.
* `DecoderParams(w_z, w_y, w_x, w_contact, w_com, grid_max_per_axis)` — placement heuristic.
* `ObjectiveWeights(unpacked, compact, com, height)` — what "better" means. For a launch
  vehicle push `com` up (6–10); for a moving truck push `compact` up.
* `Container(min_support, com_target, com_axis_weights)` — physics of the situation.
* Item `priority` — what to leave behind when it doesn't all fit (also weights the objective).

## How to make it stronger next (ordered by payoff / effort)
1. **Parallel SA restarts** (`multiprocessing`, different seeds, keep best) — variance across
   seeds is the main quality lever right now; 4 cores ≈ 4× the search for free.
2. **Incremental decoding** — a swap at sequence position *k* only invalidates placements ≥ k;
   cache the state prefix → 3–10× more SA iterations per second.
3. **Position polish** — after search, re-place single items at alternative feasible spots that
   lower the CoM deviation without touching anything else (big lever for the suitcase CoM).
4. **Voxel-occupancy collision for scanned meshes** — voxelise each mesh (e.g. 1 cm), pack
   occupancy grids instead of boxes: irregular objects nest, and support/fragile become true
   contact tests. The decoder's candidate/scoring machinery stays; only the overlap and support
   kernels change. This is the real payoff of having full lidar meshes.
5. **True cylinder collision** — replace bounding boxes for cylinder pairs with circle tests →
   nested tanks/bottles, +5–15 % utilisation on cylinder-heavy loads.
6. **Learning the sort order** — the constructive stage is 24 fixed heuristics; a GNN/transformer
   over the item set predicting a good sequence is the natural research extension.
7. **Mass-properties upgrade** — add moment-of-inertia terms and an allowable CoM *envelope*
   instead of a point target (what a vehicle integrator actually checks).

## Layout
```
packer3d/
  models.py     Item / Container / Obstacle / Placement / PackResult + validation
  decoder.py    extreme points, grid fallback, vectorised feasibility, scoring   (layer 1)
  search.py     multi-start greedy + simulated annealing, pack_naive/pack_optimized (layer 2)
  balance.py    mass-swap balancing                                             (layer 3)
  objective.py  ObjectiveWeights, metrics
  verify.py     independent re-check
  bounds.py     lower bounds, gap_report, exhaustive_small
  scenario.py   JSON scenario loader
  cli.py / visualize.py
examples/       dragon_resupply.json, suitcase.json (+ results and renders)
tests/          42 edge-case tests
```
