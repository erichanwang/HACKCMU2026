# Physics validation layer — technical reference (v2)

Validates a suitcase-packing layout: are objects inside the container, not
overlapping, adequately supported, and consistent with travel-specific
constraints (fragile, keep upright, etc.)? Ten modules, each independently
tested; `physics/validator.py` composes them into one API (§10). Teammate
-facing integration walkthrough (iOS scan → JSON → validate → solver loop →
renderer fields): `docs/INTEGRATION.md`.

**v1 → v2, in one line:** the narrow-phase math (SAT, containment) was
rewritten around a shared, vectorized per-scene precompute; support went from
bounding-rectangle footprints to exact convex-hull contact polygons with
chain-reaction propagation; constraints went from direct-only load to
transitive, area-weighted load; a new metrics module and a new incremental
per-object validator (`physics.incremental`) were added; and `validate_layout`
gained a `metrics` key and an `UNSTABLE_SUPPORT_CHAIN` warning.

## 1. Units and coordinates

- **Units**: meters, everywhere (mass in kg).
- **Axes**: X = right, Y = up, Z = forward. Right-handed.
- **Quaternion**: `(x, y, z, w)`, unit-length, world orientation of the
  object's local axes. Identity = `(0, 0, 0, 1)`.
- **Dimensions**: `(length, width, height)` — full extents (not half-extents)
  along local x/y/z, measured *before* rotation is applied. For a
  `rigidity="soft"`/`"semi"` object these are the LOOSE (uncompressed)
  dimensions (§8).
- **IDs**: stable strings, unique within a `Scene` (including no collision
  with the container's own id) — enforced by
  `scene_geometry.check_no_duplicate_ids`.

Schema (`physics/schema.py`): `Object` (id, dimensions, position, rotation,
mass_kg, constraints, rigidity, compressibility_k), `Container` (id,
dimensions, position, rotation), `Scene` (container, objects), `Constraints`
(fragile, keep_upright, cannot_support_weight, heavy, orientation_lock).

## 2. Geometry representation

Every object and the container is represented as an **OBB** (oriented
bounding box) — a center, 3 orthonormal world-space axes, 3 half-extents —
built exclusively through `physics.geometry.obb_from()` so every module
agrees on one rotation convention.

**Why OBB, not AABB or full mesh:**
- AABB (axis-aligned) throws away orientation — a shoe packed diagonally or
  a laptop stood on its edge would be tested against its axis-aligned
  bounding box, which is wrong for both containment and stacking.
- Full mesh collision is the "correct" answer but is overkill for a hackathon
  timeline: slower per-pair and far more implementation risk, for accuracy
  gains that don't matter when packable items are themselves approximated as
  boxes.
- OBB is the coarse-grained sweet spot: orientation-aware, O(1) per test,
  numerically simple (SAT reduces to a handful of dot products for box-box).

**Coarse-to-fine story**: OBB-OBB is the *only* narrow-phase implemented
today. Convex-hull/mesh narrow-phase is documented future work in
`collision.py`'s docstring, not implemented.

**Core functions** (`physics/geometry.py`):
- `quat_to_matrix(q)` — `(x,y,z,w)` → 3x3 rotation matrix; raises `ValueError`
  on a zero/non-finite-norm quaternion, defensively re-normalizes if norm is
  off by more than `1e-3` (typo-level drift) rather than silently producing a
  skewed matrix.
- `obb_from(entity)` — validates `dimensions` (finite, >0) and `position`
  (finite, shape `(3,)`), returns an `OBB(center, axes, half_extents, id)`.
  Both `Object` and `Container` use it.
- `obb_vertices(obb)` — the 8 world-space corner points, via the sign-pattern
  (`SIGNS`, fixed order) of `±half_extent` offsets rotated into world space.

**v1 → v2**: this module's math is unchanged. What moved is *orchestration*:
in v1 every submodule called `obb_from`/`obb_vertices` per object per check;
in v2 that work happens once per scene in `scene_geometry.py` (§3) and every
submodule indexes into the result instead.

## 3. Shared precompute — `SceneGeometry` (new in v2)

**Why it exists** (from the module's own docstring): profiling
`validate_layout` showed >50% of time spent rebuilding the same OBB vertices
pair-by-pair in the broad phase, with every sibling module
(containment/support/constraints) re-running `obb_from` on every object
independently. `physics/scene_geometry.py` builds everything once and every
downstream module indexes into its arrays instead of recomputing.

`precompute(scene) -> SceneGeometry` returns, in `scene.objects` order
(`index[id]` maps an id back to its row):

| Field | Shape | What |
|---|---|---|
| `container_obb`, `container_vertices`, `container_floor_y` | — / `(8,3)` / scalar | container OBB, its 8 world corners, min world-Y (the floor plane) |
| `obbs` | `list[OBB]` | one OBB per object (for callers that want the old per-object interface) |
| `centers`, `axes`, `half_extents` | `(n,3)` / `(n,3,3)` / `(n,3)` | batched OBB data; `axes[i][:,k]` is object `i`'s local axis `k` in world space |
| `vertices` | `(n,8,3)` | world-space corners, `SIGNS` order |
| `aabb_min`, `aabb_max` | `(n,3)` | world AABB per object, derived from `vertices` |
| `masses` | `(n,)` | `mass_kg` per object |
| `ids`, `index` | `list[str]` / `dict[str,int]` | id ↔ row lookup |

**Vectorized quaternion → matrix**: `_quats_to_matrices` applies the exact
same formula and re-normalization rule as `geometry.quat_to_matrix` (norm off
by `> 1e-3` → renormalize) to all `n` quaternions in one batch via
`np.einsum`, once validity is confirmed. All 8n vertices then come from a
single einsum:
`vertices[n,v,j] = centers[n,j] + Σ_k SIGNS[v,k] * half_extents[n,k] * axes[n,j,k]`.

**Malformed-input contract**: `precompute` raises `MalformedSceneError` (a
`ValueError` subclass carrying `.object_id`, naming the offending object — or
the container, or `None`) for: duplicate ids (including an object id equal to
the container's, checked first via `check_no_duplicate_ids`), non-finite or
non-positive dimensions, non-finite positions, or a zero/non-finite-norm
quaternion. The fast path validates every object with a handful of array ops
(`np.isfinite`, `dims > 0`, quaternion-norm check); only when something fails
does it fall back to a slow per-object loop (`_slow_path_raise`, calling
`geometry.obb_from` on each object in turn) so the raised message is the
single canonical one from `geometry.py`, naming the exact bad object.

**Shared helpers** built on top of `SceneGeometry`:
- `aabb_candidate_pairs(geom, epsilon=0.0)` — all `(i, j)`, `i < j`, whose
  world AABBs overlap (padded by `epsilon`). One vectorized `(n,n,3)`
  broadcast, not a Python double loop — the shared broad phase collision.py
  uses (§4), and reusable by anything else needing "which pairs are even
  close."
- `xz_overlap_area(geom, i, j)` — overlap area of two objects' XZ AABB
  footprints; 0.0 when disjoint. Exact for yaw-only rotation, an
  over-estimate otherwise — the shared *topology* test ("touching or not"),
  not a footprint area measurement (support.py computes exact footprint area
  itself, §6).
- `resting_pairs(geom, contact_eps)` — the unified "what rests on what"
  graph: `(top_idx, bottom_idx, xz_overlap_area)` for every ordered pair where
  `top`'s lowest-Y is within `contact_eps` of `bottom`'s highest-Y AND their
  XZ footprints overlap with *positive* area. Both `support.py` (`eps=1e-3`)
  and `constraints.py` (`eps=2e-2`) call this with their own tolerance —
  different epsilons for legitimately different purposes, but guaranteed to
  agree on topology given the same eps. **Caveat**: a knife-edge contact with
  zero XZ overlap area is never reported (measure zero) — see §6's caveat on
  degenerate footprints.
- `on_floor(geom, contact_eps)` — boolean mask: object's lowest-Y within
  `contact_eps` of `container_floor_y`.

**Complexity**: `precompute` is O(n), a constant number of numpy calls.
`aabb_candidate_pairs` is O(n²) but one broadcast — ~1600 comparisons at n=40
cost microseconds, not Python-loop milliseconds.

**v1 → v2**: this entire module is new. v1 had no shared precompute; every
module built its own OBBs from scratch.

## 4. Collision detection (SAT)

`physics/collision.py`: Separating Axis Theorem for two convex OBBs,
following Ericson (*Real-Time Collision Detection* §4.4.1) / Gottschalk's
OBBTree formulation. Everything is expressed in A's frame, so (unlike a naive
SAT implementation) no axis is ever built and normalized explicitly except
the single winning MTV axis at the end.

**The math.** For a pair `(A, B)` with half-extent vectors `a`, `b`:

```
R = AᵀB          R[i][j] = a_i · b_j     (A.axes.T @ B.axes)
t = Aᵀ(B.c - A.c)                        (B's center in A's frame)
```

- **Face axis `a_i`** (i=0..2): separated iff `|t_i| > a_i + Σ_j b_j·|R[i][j]|`.
- **Face axis `b_j`** (j=0..2): separated iff
  `|Σ_i t_i·R[i][j]| > b_j + Σ_i a_i·|R[i][j]|`.
- **9 cross axes `a_i × b_j`**, indices mod 3 (code hoists `(i+1)%3`/`(i+2)%3`
  as fixed tables `_I1`/`_I2` so the batched math never recomputes them):
  ```
  ra   = a_{i+1}·|R[i+2][j]| + a_{i+2}·|R[i+1][j]|
  rb   = b_{j+1}·|R[i][j+2]| + b_{j+2}·|R[i][j+1]|
  proj = |t_{i+2}·R[i+1][j] - t_{i+1}·R[i+2][j]|
  ```
  separated iff `proj > ra + rb`.

**`EPS_PARALLEL` (1e-8), and why only the cross axes get it**: it's added to
the `|R[i][j]|` terms used in `ra`/`rb` — the cross-axis radii only, not to
`proj` and not to the 6 face-axis radii. Those cross-axis radii are sums of
products of half-extents with `R` entries that all go to exactly zero when
`a_i` is parallel to `b_j`; for near-parallel edges `ra + rb` collapses to
float noise while `proj` is a difference of two nearly-equal products — the
comparison becomes numerically meaningless and can invent a separation. The
padding biases that comparison towards "not separated," i.e. conservative
(a false "colliding" on a near-degenerate axis, never a false "clear") — the
deliberate accepted tradeoff, and a change from v1's approach of *skipping*
such axes outright. The 6 face-axis radii are **not** padded: they're
`O(half-extent)` and never suffer that cancellation, so padding them would
perturb every reported face-axis penetration depth (the common case) by
`~1e-8 * Σ(half_extents)` for no benefit.

**Meters-normalization**: the 6 face-axis overlaps are already in meters
(the axes are unit vectors). The 9 cross-axis overlaps (`ra + rb - proj`) come
out scaled by `‖a_i × b_j‖ = √max(0, 1 - R[i][j]²)`, so each is divided by
that norm before comparison — this is what makes all 15 overlaps comparable
in meters, and what makes the MTV magnitude meaningful. A cross axis whose
norm is `≤ _CROSS_AXIS_MIN_NORM` (1e-8, genuinely parallel edges) is excluded
from the MTV search entirely (its "depth" is set to `+inf`) since dividing by
~0 is meaningless. This costs nothing for the collision decision: under the
padded test such an axis can never be the separating one anyway (`proj` is
exactly 0 for truly-parallel edges while the padded `ra + rb` is strictly
positive).

**Epsilon semantics** (unchanged concept from v1, now precise given the
above): `epsilon` is a distance-space slack on the *overlap along the current
axis* (in meters, post-normalization), not on the collision decision as a
whole. If `overlap <= epsilon` on *any* of the 15 axes, the boxes are **not**
colliding. Otherwise they collide, and `penetration_depth_m` is the minimum
overlap across all 15 (the MTV magnitude), `axis` its direction (oriented
A→B — sign of `axis · (B.c - A.c)`). Default `epsilon=1e-6`.

**Batched evaluation**: `_sat_batch` runs the entire test above over `m`
pairs at once — `R` is `(m,3,3)`, `t` is `(m,3)`, the 6 face overlaps are
`(m,3)` each, the 9 cross-axis quantities are `(m,9)` via the hoisted
`_I1`/`_I2` gathers, concatenated into one `(m,15)` overlap array and a
matching `(m,15)` norm array (`1`s for the 6 face axes, `‖a_i×b_j‖` for the 9
cross). `depth = overlap / norm` where usable, else `+inf`;
`k = depth.argmin(axis=1)` picks the MTV axis per pair (ties keep the
first-checked axis, since concatenation order is fixed: A's 3, B's 3, then
the 9 crosses — same tie-break rule as v1). The world direction of the
*winning* axis only is built per pair (never all 9 crosses) — a big saving
when only 1 of 15 axes needs a real vector. `contact_point` is the midpoint,
along the MTV axis, of the two boxes' overlapping projection interval,
offset to preserve the perpendicular component of the two centers' midpoint
— still a documented single-point approximation, not a true contact
manifold (support.py has the real thing for stacking contacts, §6 — this is
a different kind of contact, interpenetration not stacking).

**Public functions**:
- `check_collision(a, b, epsilon)` — one pair. A thin `m=1` wrapper around
  `_sat_batch`; there is exactly one implementation of the SAT math in this
  module.
- `check_pairs(geom, pairs, epsilon)` — SAT for every `(i,j)` row of `pairs`
  at once, indexing straight into a `SceneGeometry`'s arrays; no Python loop
  over pairs.
- `collide_scene(geom, epsilon, broad_phase_epsilon=None)` — the scene-level
  entry point: AABB broad phase (`scene_geometry.aabb_candidate_pairs`, one
  numpy broadcast) then batched narrow phase on survivors via `check_pairs`.
  `broad_phase_epsilon` (default = `epsilon`) pads the broad-phase AABBs so a
  pair can only be pruned when it's further apart than the narrow phase's own
  slack — no pair SAT would call colliding is ever lost. Returns only
  colliding results, in ascending `(i,j)` order.
- `aabb_overlap(a, b)` — per-pair broad-phase helper (8 vertices via
  `obb_vertices`, 3 interval checks) kept for callers holding only two OBBs
  (`physics.incremental` uses the scene-array equivalent directly instead,
  §11); necessary-but-not-sufficient, never a false negative.

**Complexity**: `check_collision` is O(1) — 15 fixed axes, no explicit axis
construction except the one winning axis. `check_pairs`: all `m` pairs as
`(m,15)` array ops, no Python loop. `collide_scene`: O(n²) broad phase (one
broadcast, microseconds at n≤40) + batched narrow phase on survivors — still
no spatial index.

**Measured** (per the module's own docstring, python 3.14 / numpy 2.4, single
core — not independently re-measured for this doc, see §12 for the
end-to-end numbers that were): single-pair `check_collision` ~210 µs
colliding / ~160 µs separated (vs ~700/~490 µs for the old per-axis Python
loop); `collide_scene` on a dense 20-object scene (100 of 190 pairs
overlapping) ~1.1 ms/call vs ~27 ms through per-pair `check_collision`
(~25×); per-pair cost ~11 µs batched vs ~210 µs alone — numpy call overhead
dominates at m=1, so batch whenever you can.

**Known failure modes**:
- Boxes only — concave/non-box shapes aren't modeled.
- Near-parallel edges — handled by padding, not skipping (see above); biased
  towards a false "colliding" on the degenerate axis, deliberately (a false
  "colliding" is conservative for a packing validator; a false "clear" is
  not).
- Large coordinate magnitudes (~1e6 m) degrade float64's precision; not
  addressed, fine at suitcase scale (order 1 m).
- `contact_point` is a documented single-point approximation.

**v1 → v2**: v1 built explicit, normalized cross-product axes per axis and
*skipped* near-zero ones; v2 works entirely in `R`/`t` with no explicit axis
construction except the winner, *pads* the cross-axis radii instead of
skipping, and batches everything (`check_pairs`/`collide_scene`) so the whole
narrow phase over a scene is a handful of numpy calls, not a Python loop of
15-axis checks per pair.

## 5. Containment

`physics/containment.py`: is an object OBB fully inside the container OBB?
The container may itself be rotated/positioned arbitrarily — never assumed
axis-aligned or at the origin.

**Correct-for-rotation check** (unchanged principle from v1): all 8
world-space vertices of the object are projected into the container's local
frame and compared against its half-extents per axis — checking only the
object's center is insufficient (a small rotation can poke a corner through
a wall while the center stays inside).

**Batched core**: `_containment_arrays(vertices, container_center,
container_axes, container_half_extents, epsilon)` does ONE einsum projecting
every vertex of every object into the container's local frame —
`local = einsum("nvj,jk->nvk", vertices - center, axes)` — then a
**fixed 3-iteration loop over container axes** (never over n) pulling out,
per object, the max overshoot on each of the 6 walls via masked reductions
over the 8 vertices. `check_containment` (one object) and
`check_scene_containment` (n objects) both call this one core.

**`ContainmentResult`**: `object_id`, `contained` (bool),
`penetrating_vertices` (world-space points that failed, unreduced by any
compressibility allowance — see §8), `per_wall_depth_m` (dict, container
-local wall name `{"-x","+x","-y","+y","-z","+z"}` → max overshoot, only for
walls actually violated), and two fields *derived* from that dict so old
two-field callers keep working: `penetration_depth_m =
max(per_wall_depth_m.values(), default=0.0)`, `violated_walls =
sorted(per_wall_depth_m)`.

`epsilon` (default `1e-6` m): a vertex up to `epsilon` beyond a wall still
counts as contained/touching.

`check_scene_containment(scene, epsilon, geom=None)` — batched over the
whole scene via `SceneGeometry` (built internally if `geom` isn't passed,
which still runs `check_no_duplicate_ids` + validation, so malformed scenes
raise the same `MalformedSceneError` either way); returns **only the
violating results** — a filtered list, empty means fully contained (including
the 0-object scene).

**Complexity**: one batched einsum, O(n) total (vs. v1's per-object loop).

**Limitations** (unchanged from v1): object-vs-container only (object-vs
-object is `collision.py`'s job); assumes convex box geometry; rigid-only —
compressible items are handled one layer up in `validator.py` via per-wall
allowances (§8), not inside this module.

**v1 → v2**: v1's core was a per-object loop; v2's is one batched einsum plus
a fixed 3-axis loop, and now surfaces `per_wall_depth_m` instead of only a
single worst-wall scalar — which is exactly what lets `validator.py` apply
compressibility independently per violated wall (§8) instead of picking one
wall as a stand-in.

## 6. Support and static stability

`physics/support.py` — still explicitly **a static stability heuristic, not
a full rigid-body simulator**: no force/torque integration over time, no
friction, no dynamic tipping. It answers only: "given these final resting
poses, does each object look adequately supported and balanced under gravity
alone?" v2 rewrote the geometry underneath that question to be exact.

**Model**:
- Rigid bodies, uniform density per object ⇒ COM = OBB center
  (`geom.centers[i]`); COM projection is that center's `(X, Z)`.
- Gravity along -Y.
- **Exact convex-hull contact footprints** (the headline change from v1): an
  object's bottom contact set is the subset of its 8 world corners with
  `y ≤ aabb_min.y + epsilon`; its bottom footprint is the 2D convex hull
  (monotone chain, `_hull`) of those corners' `(x,z)` — 4 points for a
  face-down box at *any* orientation, 2 for edge contact, 1 for corner
  contact. The top footprint is the analogous hull of corners with
  `y ≥ aabb_max.y - epsilon`. Because this hulls the actual contact corners
  directly rather than their axis-aligned bounding rectangle, it is exact for
  face/edge/corner contact at *any* rotation (yaw, roll, or pitch) — not just
  yaw, which was v1's limit.
- Container floor: a single flat plane at `container_floor_y` (min world-Y of
  the container OBB); its footprint is the XZ hull of the container's own 4
  lowest corners (works for a yaw-rotated or mildly tilted container; a
  heavily tilted container, where "the floor" isn't one horizontal plane, is
  out of scope).
- A support patch is the convex polygon intersection (Sutherland-Hodgman
  clip, `_clip`) of the object's bottom footprint with each supporter's top
  footprint, for every supporter from `scene_geometry.resting_pairs`
  (`epsilon=1e-3`, deliberately looser than collision/containment's 1e-6
  float-noise epsilon — this one absorbs real reconstruction/measurement
  noise between two independently-placed faces) plus the container floor
  when `scene_geometry.on_floor`. Patch area via the shoelace formula.

**PATCHES-ARE-DISJOINT ASSUMPTION**: `covered_area = Σ patch areas`, clamped
to the bottom footprint area. Exact for a collision-free scene (two
supporters touching the same object at the same height can't overlap each
other in XZ without interpenetrating); in an already-colliding scene
(collision is checked separately) overlapping supporters would be
double-counted, capped at ratio 1.0 by the clamp.

**Degenerate contact** (constants `DEGENERATE_AREA_M2 = 1e-9`,
`TOUCH_TOL_M = 1e-9`): when the bottom footprint hull's area is below
`DEGENERATE_AREA_M2` (edge or corner contact — a segment or a single point),
area ratios are meaningless, so `support_ratio` becomes the fraction of the
bottom *contact points* that lie inside, or within `TOUCH_TOL_M` of, some
supporter's top footprint (floor included). A box balanced on one bottom edge
whose two contact corners both land on a supporter reads 1.0; one corner
reads 0.5.

**Stability**: the support polygon is the convex hull of the union of all
support-patch vertices; `stability_margin_m` is the signed 2D distance from
the COM projection to the nearest point of that hull's boundary — positive
inside, negative outside (stable iff COM projects inside the support
polygon). A degenerate support polygon (segment/point, from edge/corner
contact) has no "inside," so the margin is always `≤ 0` there (distance to
it, negated). **Tie-break at margin 0**: a margin in `(-1e-9, 0)` is snapped
to exactly `0.0`, so a COM sitting exactly on the support polygon's boundary
(a perfectly edge-balanced box) reads `margin=0.0`, `unstable=False`
(`unstable = margin < 0`) — boundary counts as supported, float noise doesn't
decide the verdict. No support at all (empty support polygon) reports the
sentinel `stability_margin_m = FLOATING_MARGIN_SENTINEL_M = -1.0e6`, not a
real distance.

**Chain instability (`supported_by_unstable`, new in v2)**: `True` when any
object this one rests on is itself `floating`, `unstable`, or
`supported_by_unstable`. Computed bottom-up in ascending `aabb_min.y` order
(stable sort — a supporter's bottom is always ≤ its supportee's, so a single
pass visits supporters before what rests on them), so "A destabilizes B
destabilizes C" propagates through an entire stack. This closes v1's "no
chain-reaction modeling" limitation.

**`SupportResult` fields**: `object_id`, `support_ratio` (clamped to 1.0),
`stability_margin_m`, `supporting_objects` (ids, `"container_floor"`
included), `floating` (`support_ratio < floating_threshold`, default 0.05,
unchanged from v1), `unstable` (`margin < 0`), `supported_by_unstable`
(**new**), `contact_polygon` (**new** — `[[x,z], ...]` support-hull vertices,
empty when unsupported; renderer diagnostic), `patch_areas_m2` (**new** —
supporter id → patch area m², `"container_floor"` included).

**Caveat — knife-edge / measure-zero contacts**: `resting_pairs` never
reports a pair whose XZ overlap area is exactly zero (a true knife-edge, e.g.
one object's bottom edge line exactly touching another's top edge line with
no area). Such a pair never becomes an entry in `supporters[i]`, so it's
never offered as a clip candidate at all — even though the degenerate
-contact point-in-polygon fallback above would have handled it as a
supporter if it had been offered. In practice this only matters for
razor-exact, area-zero alignments; any real contact area (however small)
still works.

**Complexity**: O(n) hull precompute + `resting_pairs`'s vectorized O(n²)
height/AABB test + a polygon clip per surviving (object, supporter) pair (a
handful per object in practice). Fine at hackathon scale (5-20 objects).

**Still-open limitations**: no friction (an object obviously wall/neighbor
-braced by friction is judged on footprint/COM alone); no dynamics (no
normal-force distribution solved — a supported-but-overloaded object looks
the same as a sound one here; mass only reaches the constraints layer, §7);
uniform density; single flat floor plane; patches assumed disjoint (above);
rigid bodies — `rigidity`/`compressibility_k` aren't consulted here, so a
squashed soft item's real (larger) contact patch isn't modeled by this
module (compressibility is handled separately, §8, and doesn't feed back
into support footprints).

**v1 → v2**: v1 approximated contact footprints as XZ bounding rectangles of
an object's 4 lowest/highest vertices (exact only for yaw-only rotation) and
measured `stability_margin_m` against the merged *bounding rectangle* of all
contributing regions; v2 replaces both with exact convex-hull contact
polygons and Sutherland-Hodgman-clipped patches (exact at any rotation), and
adds chain-reaction propagation (`supported_by_unstable`) that v1 explicitly
did not model.

## 7. Travel constraint layer

`physics/constraints.py` — optional, opt-in metadata layer. No-op when every
object's `Constraints` is left at defaults; only flags what an object
explicitly opts into.

| Constraint field | Check | Result type | Kind |
|---|---|---|---|
| `keep_upright` | local Y axis tilt from world up > `angle_tol_deg` (default 15°) | `LIQUID_NOT_UPRIGHT` | violation |
| `orientation_lock="this_side_up"` | same test as `keep_upright` | `INVALID_ORIENTATION` | violation |
| `orientation_lock="flat_only"` | local Y axis not within tolerance of ±world-Y (lying on its largest face) | `INVALID_ORIENTATION` | violation |
| `orientation_lock="horizontal"` | local Y axis not within tolerance of the XZ plane | `INVALID_ORIENTATION` | violation |
| `cannot_support_weight` | nonzero **transitive** load rests on top | `FRAGILE_OBJECT_OVERLOADED` | violation |
| `fragile` | nonzero transitive load rests on top, OR footprint overlaps a `heavy` object's footprint at ~same height | `FRAGILE_LOAD` | warning |
| `heavy` | **the heavy object itself rests on top of anything** (has ≥1 direct supporter) | `HEAVY_ON_TOP` | warning (always emitted when true, independent of what's underneath) |

**Direction check for `HEAVY_ON_TOP`** (easy to misread, verified against
`tests/test_constraints.py::test_heavy_object_stacked_produces_warning`):
the warning's `object_id` is the **heavy object**, and it fires because *it*
is resting on something — not because something is resting on it. A heavy
item stacked above other items gets flagged regardless of what's underneath.

**Contact topology**: `scene_geometry.resting_pairs(geom, contact_eps_m=0.02)`
— constraints.py's own default eps, distinct from support.py's `1e-3` (§3);
`(top, bottom, area)` triples, rotation-aware to the extent an AABB is (exact
for yaw, conservative/over-estimating otherwise — `xz_overlap_area`). The
`fragile`/`heavy` "adjacent" check reuses `xz_overlap_area(geom,i,j) > 0.0`
directly, without the height requirement — a footprint-proximity
approximation, not true 3D side-contact.

**Load model — transitive, area-weighted propagation (rewritten in v2)**. A
resting DAG can stack more than one level deep (a shoe on a toiletry bag on
a laptop): the laptop is loaded by the *whole* stack above it, not just the
bag directly touching it.

```
load[x] = mass[x] + Σ_{y rests on x} load[y] · w_yx
w_yx    = area_yx / Σ_{z : y rests on z} area_yz
supported_weight_kg[x] = load[x] - mass[x]
```

Each object's accumulated load (its own mass plus everything piled on it) is
split among *its own* direct supporters in proportion to contact overlap
area — one supporter takes 100%, two equal-area supporters split it 50/50.
Computed in one pass, processing objects top-down (descending `aabb_min[:,1]`,
so a resting DAG's leaves are visited before its roots) and pushing each
object's now-final `load` onto its own direct supporters — O(E) over the
resting-pair edges after an O(n log n) sort, dominated by the O(n²) topology
build. Collapses to v1's direct-sum definition for single-level stacks (one
supporter, `w=1`).

Both the transitive `supported_weight_kg` and the old direct definition
`direct_weight_kg` (sum of mass of objects *directly* on top, no propagation)
are reported side by side in violation/warning details — direct weight is
still a useful "what's touching this" number for a renderer.

`HEAVY_ON_TOP.details.load_path`: from the heavy object down to the
floor-level (no-supporters) object, following the heaviest-loaded supporting
edge at each step (the direct supporter receiving the largest share of that
node's load — equivalent to the largest-area supporter, since a node's own
load is fixed when comparing its own supporters). Cycle-safe (tracks visited
nodes and stops rather than looping).

**Complexity**: O(n²) topology build (one vectorized broadcast) + O(n log n)
sort + O(E) propagation pass.

**v1 → v2**: v1 summed only *direct* weight resting immediately on top of an
object. v2 propagates load transitively through multi-level stacks,
area-weighted across an object's own supporters, and reports both numbers.
`HEAVY_ON_TOP` also effectively changed emphasis: it's about the heavy object
resting on something (with a `load_path` down to the floor), not about what's
piled on a heavy object.

## 8. Compressibility (soft/semi-rigid items)

`physics/compressibility.py` — layered on top of `collision.py`/
`containment.py` inside `validator.py`, not inside those modules themselves
(they stay rigid-box-only). Two `Object` fields drive it: `rigidity`
(`"rigid"`|`"semi"`|`"soft"`, default `"rigid"`) and `compressibility_k`
(`>= 1.0`, default `1.0`, ignored when `rigidity="rigid"`). `dimensions`
always stays the object's *loose* (uncompressed) size — the allowance below
is how much of that loose size can plausibly squish into an occupied void,
not a resized OBB.

**Heuristic** (explicitly crude — a single scalar per axis, not a
deformation model):

```
compression_allowance_m(obj, extent_m) =
    0                                                        if rigid
    clamp(extent_m * (1 - 1/k) * fraction, 0, 0.95*extent_m) otherwise
```

`fraction = RIGIDITY_ALLOWANCE_FRACTION[rigidity]` = `{semi: 0.5, soft: 1.0}`,
`extent_m` is the object's own full extent along whichever axis is being
checked (a collision MTV axis, or its own extent projected onto a violated
container wall's axis via `axis_projected_extent_m`). The 95% cap prevents an
object from ever being treated as compressing its entire size away. A rigid
object always gets zero allowance regardless of `compressibility_k`.

**Where it plugs into `validate_layout`**:
- **Collision**: for a colliding pair, `combined_collision_allowance_m(a, b,
  mtv_axis, obb_a, obb_b)` sums each object's own allowance (each projected
  onto the actual MTV axis) and subtracts it from the raw SAT
  `penetration_depth_m`. Fully absorbed → downgraded from `OBJECT_COLLISION`
  to a `SOFT_COMPRESSION` warning; otherwise it's still a violation, sized by
  the *excess* only.
- **Containment — now per-wall (v2 change)**: since `containment.py` now
  reports `per_wall_depth_m` (§5), each *violated* wall gets its own
  allowance, computed from the object's extent projected onto that specific
  wall's axis (`container_wall_allowance_m`), and its own depth is reduced
  independently: `effective_depth = max(reduced depth over all walls)`. v1's
  doc described a workaround here ("no per-wall breakdown existed, so the
  first-sorted violated wall stood in for the axis") — that workaround is
  gone; the real per-wall depth is what gets reduced now.

Rigid-only scenes are byte-for-byte unaffected (allowance is always exactly
`0.0`).

**Note on `SOFT_COMPRESSION`'s `compressed_depth_m` field**: in the current
code (both the containment and collision branches in `validator.py`), this
field is set to the *raw* penetration depth (`"compressed_depth_m": raw_depth`),
identical to `raw_penetration_depth_m` in the same dict — not to the
post-compression residual (which is ≤0 by construction whenever this warning
fires, since it only fires when the allowance fully absorbs the overlap).
Documented here as observed; treat the two fields as duplicates for now, not
as "raw vs. remaining."

## 9. Metrics (new in v2)

`physics/metrics.py` — `scene_metrics(geom) -> dict`, JSON-dumpable (plain
floats/lists/dicts, no numpy scalars). Purely informational: **never affects
`valid`/`score`/violations/warnings** — these are solver objective terms.

| Field | Meaning | Caveat |
|---|---|---|
| `total_mass_kg` | Σ `mass_kg` | — |
| `center_of_mass` | mass-weighted centroid of object OBB centers, world-space `[x,y,z]` | uniform-density assumption (same as support.py) |
| `com_offset_m` | `center_of_mass - container.center`, expressed in the **container's local axes** (`@ container.axes`) | lets a solver push mass toward a fixed side (e.g. "the wheel end") regardless of how the container itself is rotated in world space — same convention `containment.py` uses for wall projections |
| `fill_ratio` | Σ object OBB volumes / container OBB volume | a volume ratio of each object's own OBB, not a collision-aware "occupied volume"; a colliding scene (not a valid one) would double-count overlap |
| `per_object[id].wall_clearance_m` | min, over 8 vertices × 3 wall axes, of `half_extent - |local coord|` | same projection as `containment.py`, but unfiltered — can go **negative** if penetrating |
| `per_object[id].nearest_neighbor_gap_m` | AABB gap to the nearest other object, clamped ≥0, `None` if n≤1 | **AABB-based** — conservative (under-estimate) for rotated boxes, since a rotated OBB's AABB is looser than the box itself; 0 whenever AABBs overlap |
| `per_object[id].nearest_neighbor_id` | id of that neighbor | `None` if n≤1 |
| `per_object[id].height_above_floor_m` | `aabb_min.y - container_floor_y` | — |
| `per_object[id].footprint_area_m2` | object's XZ **AABB** footprint area | **AABB-based** — over-estimates the true footprint for anything not yaw-aligned, unlike support.py's exact convex hull (§6); different tool, different tradeoff |
| `per_object[id].volume_m3` | object's own OBB volume | exact — a box's own volume doesn't change under rotation |

**Complexity**: O(n) for mass/fill/wall-clearance (one einsum over `(n,8,3)`,
same projection `containment.py` uses); O(n²) for nearest-neighbor gaps (one
`(n,n,3)` broadcast) — same tradeoff as `aabb_candidate_pairs`.

**v1 → v2**: entirely new; v1 had no metrics module.

## 10. `validate_layout()` API

```python
def validate_layout(scene: Scene, *, floating_threshold: float = 0.05) -> dict:
    """
    {
      "valid": bool,        # True iff violations is empty
      "score": float,       # 1.0 clean -> lower as violations pile up/worsen
      "violations": [...],  # hard failures
      "warnings": [...],    # soft/informational, never affect valid/score
      "metrics": {...},     # physics.metrics.scene_metrics (new in v2)
    }
    """
```

**Pipeline order** (from the module's own docstring):

```
precompute (scene_geometry)
  -> containment  (vectorized, per-wall depths)
  -> collision    (AABB broad phase + batched 15-axis SAT)
  -> support      (exact contact polygons, static stability, chains)
  -> constraints  (travel metadata, transitive load)
  -> metrics      (COM, fill, clearances -- solver objective terms)
```

**Malformed-input guard**: `precompute` validates the container and every
object up front (finite/positive dims, finite position, non-zero finite
quaternion, unique ids). Any `MalformedSceneError` (or an untagged
`ValueError` — defensive, for anything `geometry.py` raises that isn't
wrapped) short-circuits to `{"valid": False, "score": 0.0, "violations":
[{"type": "MALFORMED_GEOMETRY", "object": <id or None>, "detail": <msg>}],
"warnings": [], "metrics": {}}` — note the `"metrics": {}` (new in v2; v1's
malformed shape had no `metrics` key at all). Function **never raises**.

**Violation types**: `MALFORMED_GEOMETRY`, `CONTAINER_PENETRATION`,
`OBJECT_COLLISION`, `UNSUPPORTED_OBJECT`, `LIQUID_NOT_UPRIGHT`,
`INVALID_ORIENTATION`, `FRAGILE_OBJECT_OVERLOADED`.
**Warning types**: `SOFT_COMPRESSION`, `UNSTABLE_STACK`,
`UNSTABLE_SUPPORT_CHAIN` (new in v2), `FRAGILE_LOAD`, `HEAVY_ON_TOP`.

**Complete field table** (every field each entry carries; `object`/`objects`
and `type` are on every row so omitted from the "Fields" column below):

| Type | Kind | Fields |
|---|---|---|
| `MALFORMED_GEOMETRY` | violation | `object` (id or `None`), `detail` (str) |
| `CONTAINER_PENETRATION` | violation | `object`, `penetration_depth_m` (post-compression), `violated_walls`, `per_wall_depth_m` (only walls still >0 after allowance), `penetrating_vertices` (raw, `[x,y,z]` each — **not** reduced by compression), `severity` |
| `OBJECT_COLLISION` | violation | `objects` (`[id,id]`, lexicographically sorted), `penetration_depth_m` (post-compression), `contact_point` (`[x,y,z]`), `axis` (`[x,y,z]`, MTV direction — see caveat below), `severity` |
| `UNSUPPORTED_OBJECT` | violation | `object`, `support_ratio`, `center_of_mass_projection` (`[x,z]`), `severity` |
| `LIQUID_NOT_UPRIGHT` | violation | `object`, `tilt_deg`, `tolerance_deg`, `severity` |
| `INVALID_ORIENTATION` | violation | `object`, `lock` (the `orientation_lock` string), `tilt_deg`, `tolerance_deg`, `severity` |
| `FRAGILE_OBJECT_OVERLOADED` | violation | `object`, `supported_weight_kg` (transitive), `direct_weight_kg`, `severity` |
| `SOFT_COMPRESSION` (containment) | warning | `object`, `raw_penetration_depth_m`, `compressed_depth_m` (== raw, see §8 note), `walls` (all originally-violated walls) |
| `SOFT_COMPRESSION` (collision) | warning | `objects`, `raw_penetration_depth_m`, `compressed_depth_m` (== raw), `contact_point` |
| `UNSTABLE_STACK` | warning | `object`, `stability_margin_m`, `support_ratio`, `supporting_objects`, `center_of_mass_projection`, `contact_polygon` (`[[x,z],...]`) |
| `UNSTABLE_SUPPORT_CHAIN` | warning | `object`, `supporting_objects` |
| `FRAGILE_LOAD` | warning | `object`, `supported_weight_kg` (transitive), `direct_weight_kg`, `adjacent_heavy` (bool) |
| `HEAVY_ON_TOP` | warning | `object` (the heavy object), `resting_on` (ids it rests on), `load_path` (heavy object → floor) |

**Caveat on `OBJECT_COLLISION.axis`**: `axis` is oriented A→B using
`collide_scene`'s internal `SceneGeometry` index order (`i < j`), which is
*not* guaranteed to match the lexicographic order of the reported `objects`
list (`objects` is always `sorted([a.id, b.id])` for determinism, §10
"Determinism" below). There's no `a_id`/`b_id` in the emitted dict to
disambiguate — if you need to know which end `axis` points away from,
you'd need to re-derive it; don't assume `axis` points from `objects[0]` to
`objects[1]`.

**Hard-fail vs. warning policy**:
- `OBJECT_COLLISION` / `CONTAINER_PENETRATION` are hard fails *after*
  subtracting the compressibility allowance (§8); rigid objects have zero
  allowance so rigid-only scenes behave exactly as plain geometry.
- `UNSUPPORTED_OBJECT` (floating) is a hard fail.
- `UNSTABLE_STACK` / `UNSTABLE_SUPPORT_CHAIN` / floating are mutually
  exclusive per object, checked in that priority order (floating wins over
  unstable, unstable wins over chain-unstable): a supported-but-off-balance
  object is only a warning (precarious, not impossible — no
  friction/tip-over dynamics, §6); an otherwise-fine object resting on
  something floating/unstable/chain-unstable gets the (new in v2)
  `UNSTABLE_SUPPORT_CHAIN` warning instead.
- Constraint passthrough: `LIQUID_NOT_UPRIGHT`/`INVALID_ORIENTATION`/
  `FRAGILE_OBJECT_OVERLOADED` stay violations; `FRAGILE_LOAD`/`HEAVY_ON_TOP`
  stay warnings.

**Severity formulas** (each clamped to `[0,1]`; used only for `score`):

| Type | Severity |
|---|---|
| `OBJECT_COLLISION` | `penetration_depth_m / min(smallest full extent of A, smallest full extent of B)` |
| `CONTAINER_PENETRATION` | `penetration_depth_m / smallest container half-extent` |
| `UNSUPPORTED_OBJECT` | `1 - support_ratio / floating_threshold` |
| `FRAGILE_OBJECT_OVERLOADED` | `supported_weight_kg / max(0.1, object's own mass_kg)` |
| `LIQUID_NOT_UPRIGHT` / `INVALID_ORIENTATION` | `(tilt_deg - tolerance_deg) / (90 - tolerance_deg)` |
| `MALFORMED_GEOMETRY` | none — short-circuits, score forced to `0.0` |

`score = max(0, 1 - Σ min(1, severity) / max(1, n))`, `n = len(scene.objects)`.
Warnings never affect score. Monotonic: adding a violation or increasing any
severity can only lower or hold the score.

**Determinism**: no randomness anywhere; `violations` and `warnings` are
each sorted by `(type, object_id)` (a collision/soft-compression pair uses
its lexicographically-smaller id) before being returned — repeated calls on
the same scene produce byte-identical `json.dumps` output.

**v1 → v2**: pipeline order is the same shape (containment → collision →
support → constraints) with a `metrics` pass appended every run; the output
gained a `metrics` key (always present, even on a clean-but-empty `{}` for
malformed input) and a new `UNSTABLE_SUPPORT_CHAIN` warning type that v1
never computed at all (v1 had no chain-reaction modeling to report).

## 11. Integration

Full teammate-facing walkthrough with JSON and CLI examples:
**`docs/INTEGRATION.md`**. This section is the technical contract.

**`physics/io.py`** — three jobs, all pure functions over `physics.schema`
dataclasses (nothing here re-implements physics; it's the boundary):
1. **Round-trip**: `scene_to_dict`/`scene_from_dict` (and per-type
   `object_to_dict`/`object_from_dict`, `container_to_dict`/
   `container_from_dict`, `constraints_to_dict`/`constraints_from_dict`),
   plus `result_to_json` — `json.dumps` with a `default=` hook that unwraps
   `np.generic`/`np.ndarray` (some result fields, e.g. `support_ratio`, can
   carry numpy scalars).
2. **LiDAR scanner adapters**:
   - `object_from_scanned_item(item, position=origin, rotation=identity, id=None, **fields)`
     — a `ScannedItem` dict is **centimetres**, no pose. Divides
     `width`/`depth`/`height` by 100 and maps `width → dims[0]` (local x),
     `height → dims[1]` (local y, vertical), `depth → dims[2]` (local z).
     `position`/`rotation` default to origin/identity since the scanner
     supplies none — a legitimate input to hand the packing *solver* (which
     only cares about dimensions), **not** a meaningful input to
     `validate_layout`/`validate` until it's actually been placed.
   - `object_from_box_fit(id, width_m, depth_m, height_m, center, axis, **fields)`
     — a `BoxFit` is **metres**, yaw-only world pose. `axis=(ax,0,az)` is the
     world unit vector of the box's width edge; rotating local +x about
     world Y by θ gives `(cosθ, 0, -sinθ)`, so matching that to `axis` gives
     **`θ = atan2(-az, ax)`**, quaternion `(0, sin(θ/2), 0, cos(θ/2))`.
     Verified numerically in `tests/test_io.py`
     (`TestBoxFitOrientation`): reconstructing `obb_vertices` of the
     resulting `Object` reproduces `axis` and `width_m` exactly, for a swept
     range of angles.
3. **Solver placement adapters**:
   - `apply_placements(scene, placements) -> Scene` — returns a **new**
     `Scene` (never mutates `scene` or `placements`), matching each
     placement dict by `id`. Accepts **either** `position`/`rotation` **or**
     `target_position`/`target_rotation` keys (both spellings appear across
     the team's docs). Objects not mentioned keep their current pose.
     Unknown ids raise `ValueError` listing them.
   - `validate(scene, placements=None) -> dict` — applies placements (if
     given) then calls `validate_layout`. Never raises: an unknown placement
     id becomes a single `MALFORMED_GEOMETRY` violation (`object` = the
     first unknown id) instead of propagating `apply_placements`'s
     `ValueError`.

**CLI** (`physics/__main__.py`, `python3 -m physics ...`):

| Command | Does |
|---|---|
| `validate scene.json [--placements p.json] [--pretty]` | `scene_from_dict` → `validate(scene, placements)` → prints `result_to_json`. `--placements` file may be a bare list or the `{"placements": [...]}` wrapper — both accepted. |
| `example` | Prints `tests.fixtures.valid_packed_scene()`'s JSON shape. |
| `scan-to-object item.json` | `ScannedItem` JSON (cm) → `Object` dict (m), via `object_from_scanned_item`. |

Exit codes: `0` valid, `1` ran fine but found violations, `2` unreadable/
malformed *input file* (`OSError`/`JSONDecodeError`/`KeyError`/`TypeError`/
`ValueError` while loading — distinct from a validation failure).

**`physics/incremental.py` — `PlacementValidator`**: the packing solver's
inner-loop optimization. `validate_layout` re-validates an *entire* scene
(O(k²)) every time the solver asks "I have k objects placed, can I add this
one here?" `PlacementValidator` instead keeps a small growing cache of the k
already-committed objects and evaluates only the **one new object** against
that cache: **O(k) per query** — a vectorized AABB prefilter over the cached
`(k,3)` min/max arrays, then O(1) narrow-phase SAT only on survivors; support
and fragile/heavy checks are similarly O(k) (vectorized height compare +
Python loop over contact survivors only); containment/orientation are O(1).
`place`/`remove` are O(k) (`np.vstack` append / boolean-mask rebuild).

- `try_place(obj) -> dict` — evaluate, don't commit.
- `place(obj, force=False) -> dict` — evaluate, commit if valid (or
  `force=True`).
- `remove(object_id)` — drop from the cache.
- `incremental_metrics(obj) -> dict` — a raw metrics helper (`nearest_neighbor_gap_m`,
  `wall_clearance_m`), **not** part of the validation pipeline; unlike
  `try_place`, this one raises `ValueError` directly (via `obb_from`) on
  malformed input.
- `to_scene() -> Scene` — the committed objects, for a final authoritative
  pass.

Result shape matches `validate_layout`'s `{"valid","score","violations",
"warnings"}` (**no `metrics` key**), same violation/warning types and field
shapes, but describing only `obj` against the committed state — and `score`
uses `n=1` (`score = max(0, 1 - Σ min(1,severity))`), not divided by scene
size, since exactly one object is evaluated per call.

**What is NOT re-checked**: previously-committed objects' mutual validity
(pairwise collision/containment/support/orientation among each other) is
never re-verified on a query — that was already checked when each was
placed. Revalidating everyone on every call is exactly the O(k²) cost this
module exists to avoid; it's the caller's responsibility to only place
objects whose own `try_place`/`place` was valid, or that were force-committed
deliberately. **One documented exception**: placing a new object on top of
an existing one can flip that existing object's `cannot_support_weight`
status — `try_place`/`place` check the new object's direct supporters for
that flag and emit `FRAGILE_OBJECT_OVERLOADED` for the *supporter's* id.
Nothing else about committed objects is re-derived (a supporter's own
`FRAGILE_LOAD`/`HEAVY_ON_TOP` isn't recomputed on every query — cheap to
catch in the final full pass instead).

**Approximations vs. the full validator** — `PlacementValidator` **replicates**
small private versions of the support/orientation math rather than importing
`support.py`/`constraints.py`. Per its own docstring, the stated reason was
that those two modules were being rewritten concurrently with this module and
only `check_collision`/`check_containment` were "contractually stable" at the
time — see the note at the end of this section. The concrete divergences:
- **Footprint**: uses the object's full XZ-**AABB**, not support.py's exact
  convex hull of actual contact corners — identical to support.py's
  approximation for yaw/axis-aligned objects (the common case; every test
  fixture is axis-aligned), looser (larger) for a rolled/pitched object, and
  has no degenerate edge/corner-contact handling at all.
- **`support_ratio`**: sum of each clipped supporter rectangle's area
  (double-counts overlapping supporters under the same object), not
  support.py's exact coordinate-compression union. Marked `# ponytail:` in
  the source as a deliberate shortcut (upgrade path: the coordinate
  -compression union, if double-counted overlapping-supporter stacks become
  common). Conservative in the safe direction for a solver — only ever
  pushes the ratio *up* toward 1.0, never inflates a genuinely floating
  object.
- **One `contact_eps`** (default `1e-3` m) serves both "is this resting on
  X" and the fragile/heavy adjacency rule, where `validate_layout` uses two
  different epsilons (support.py's `1e-3`, constraints.py's `0.02`) — net
  effect, `PlacementValidator` is slightly *stricter* about "resting on" for
  fragile/heavy purposes (misses only a 1mm–2cm gap; flush-packed items never
  have one).
- **No transitive load, no `UNSTABLE_SUPPORT_CHAIN`, no `contact_polygon`/
  `patch_areas_m2`, no metrics.**
- **Field shapes are a strict subset of `validate_layout`'s** (§10's field
  table), not just a scoping difference: no `axis` on `OBJECT_COLLISION`; no
  `per_wall_depth_m`/`penetrating_vertices` on `CONTAINER_PENETRATION`; no
  `support_ratio`/`supporting_objects`/`contact_polygon` on `UNSTABLE_STACK`;
  no `center_of_mass_projection` on `UNSUPPORTED_OBJECT`; no
  `direct_weight_kg` on `FRAGILE_OBJECT_OVERLOADED`/`FRAGILE_LOAD` (and
  `FRAGILE_LOAD.supported_weight_kg` here is always hardcoded `0.0`, since
  the incremental path only checks adjacency, never load); no `load_path` on
  `HEAVY_ON_TOP`; no `walls`/`contact_point` on the containment/collision
  `SOFT_COMPRESSION` shapes respectively. Same `type` names, thinner
  payloads — don't rely on the incremental path for renderer diagnostics
  beyond `valid`/`score`; those extras only exist once `validate_layout` runs
  the final pass.

**A note on that "concurrently being rewritten" framing**: as of this
rewrite, `support.py` and `constraints.py` have already landed in the exact
-hull / transitive-load form described in §6–§7 — they are not, in fact,
still being rewritten. `physics/incremental.py`'s replicated logic has not
been updated to track that; it still reflects the *older* bounding-rect
-footprint / direct-only-weight model those modules used before this pass.
So the gap between `PlacementValidator` and `validate_layout` is larger
today than the module's own "Divergences" list (written against the old
support/constraints) implies — not a temporary, soon-resolved gap. Treat the
list above as current fact, not the module docstring's framing of *why* it
exists.

**Recommendation** (from the module's own docstring, worth repeating): run
`validate_layout(placement_validator.to_scene())` **once**, as the
authoritative full check, when the solver is done. `PlacementValidator`
optimizes the hot inner loop; it does not replace the full pipeline's
guarantees.

## 12. Performance

**Complexity**: O(n) precompute + O(n²) vectorized broad phase (collision
AABB pairs, and separately support/constraints' resting-pair topology) +
batched narrow-phase work only on survivors. No spatial index anywhere in
this layer — a grid/BVH is the upgrade path beyond hackathon scale.

**Benchmark — `PYTHONPATH=. python3 tests/benchmark_validator.py`** (this
machine: Python 3.14.4, numpy 2.4.6, x86_64, single run, 30 reps/scene after
one warm-up call):

| scene | n | ms/call | collisions |
|---|---|---|---|
| sparse (no AABB overlap) | 5 | 0.83 | 0 |
| sparse (no AABB overlap) | 10 | 0.87 | 0 |
| sparse (no AABB overlap) | 20 | 1.09 | 0 |
| sparse (no AABB overlap) | 40 | 1.81 | 0 |
| touching (flush, SAT separates) | 5 | 0.95 | 0 |
| touching (flush, SAT separates) | 10 | 1.02 | 0 |
| touching (flush, SAT separates) | 20 | 1.29 | 0 |
| touching (flush, SAT separates) | 40 | 1.94 | 0 |
| dense (overlapping) | 5 | 1.41 | 8 |
| dense (overlapping) | 10 | 1.59 | 22 |
| dense (overlapping) | 20 | 2.09 | 55 |
| dense (overlapping) | 40 | 3.05 | 124 |

(Measured after the support/constraints hot-path pass, which is guarded by
`tests/test_snapshot_regression.py`: 50 scenes' full `validate_layout` JSON
is bit-identical before and after that optimization.)

**Benchmark — `PYTHONPATH=. python3 tests/benchmark_incremental.py`** (same
machine/run; 20 objects already committed, 1000 `try_place` calls of a 21st
overlapping candidate vs. 200 full `validate_layout` calls on the equivalent
21-object scene):

```
try_place: 195.20 us/call over 1000 calls (20 committed objects, 21-object scene)
validate_layout (full 21-object scene): 1176.93 us/call
speedup: 6.0x
```

**Reading these numbers**: single-run, single-machine timings — expect
variance run to run and hardware to hardware; treat the magnitudes, not the
decimals. Growth from n=5→40 (8×) is roughly 3-5× in wall time across all
three families, sub-quadratic in practice at this scale — at n≤40 the fixed
Python/numpy dispatch overhead per call dominates over the O(n²) term (the
same effect `collision.py`'s own docstring calls out: numpy call overhead
dominates at small batch sizes). `PlacementValidator.try_place` is ~6×
faster than a full `validate_layout` re-run at k=20 committed objects (the
full validator got fast enough that the gap narrowed from ~13× to ~6×) —
and the gap grows with k, since `try_place` stays O(k) while
`validate_layout` stays O(k²).

**v1 → v2**: v1 measured ~11.6 ms for 20 sparse objects and ~41 ms for 40
(per-pair Python SAT, vertices rebuilt per pair, OBBs rebuilt 4× per
object); a dense 20-object scene with 100 colliding pairs took ~30 ms in the
narrow phase alone. v2 is ~10× faster on the sparse case and makes the
dense case routine (2.1 ms with 55 collisions). v1's benchmark also never
exercised the narrow phase — a sparse grid has no AABB overlaps — so v2 adds
the sparse/touching/dense distinction and the incremental-vs-full
comparison.

## 13. Known limitations (consolidated)

**Closed since v1** (do not assume these still apply):
- ~~Bounding-rectangle footprints in support.py~~ — replaced by exact
  convex-hull contact patches (§6).
- ~~No chain-reaction modeling in support~~ — `supported_by_unstable`
  propagates instability up a stack (§6).
- ~~SAT skips near-parallel-edge axes entirely~~ — now padded, not skipped
  (§4); a related, narrower edge case remains (see below).
- ~~Direct-only load in constraints~~ — load is now transitive and
  area-weighted (§7).

**Still open**:
- **No friction model** anywhere (collision, containment, support) — an
  object braced by friction is judged purely on geometry/footprint.
- **No dynamic simulation** — no forces/torques integrated over time, no
  tipping under acceleration/vibration/braking/drops; support is a static,
  single-instant heuristic; no normal-force distribution is solved (a
  supported-but-overloaded object looks the same as a sound one to
  support.py — mass only reaches the constraints layer).
- **Uniform density** — COM is always the geometric OBB center, not a real
  mass distribution.
- **Single flat floor plane** — a heavily tilted container is out of scope
  for support.py.
- **AABB-based topology/metrics caveats** — support.py's *own* contact
  footprints are now exact hulls (§6), but `constraints.py`'s resting-pair/
  adjacency topology and `metrics.py`'s `nearest_neighbor_gap_m`/
  `footprint_area_m2` still use AABB-based approximations for performance
  (exact for yaw, conservative or over-estimating otherwise — see §7, §9).
- **Compressibility is a crude per-axis scalar** (§8), not a real
  deformation model; anisotropic, load-dependent real-world squish isn't
  modeled. `SOFT_COMPRESSION.compressed_depth_m` currently duplicates
  `raw_penetration_depth_m` rather than reporting a residual (§8).
- **OBB-only narrow phase** — no convex-hull/mesh collision; documented
  future work in `collision.py`, not implemented.
- **PyBullet (or similar) full rigid-body final-check** is a documented
  future option per the project's PRD/MVP docs — not implemented anywhere in
  this layer.
- **Near-parallel-edge SAT bias** — a padded, not skipped, degenerate axis
  can rarely misreport two boxes separated only along it as colliding
  (deliberately conservative, §4).
- **Float precision at large coordinates** (~1e6 m) is not addressed; fine
  for suitcase-scale scenes (order 1 m).
- **`contact_point` in collision.py** remains a single approximate point
  (midpoint of the MTV overlap interval), not a true contact manifold — a
  different, narrower thing than support.py's exact `contact_polygon`, which
  covers stacking contacts, not interpenetration.
- **`UNSTABLE_STACK`/`UNSTABLE_SUPPORT_CHAIN` are warnings, not hard fails**
  — a scene with precariously balanced (but supported) objects is still
  `valid: True`; no friction/tip-over dynamics back this judgment (§6).
- **Support's patches-disjoint assumption** only holds in a collision-free
  scene (§6); knife-edge (zero-area) contacts are never reported by
  `resting_pairs` (§3, §6).
- **`PlacementValidator` runs an older, coarser approximation** of support/
  orientation logic than the current `support.py`/`constraints.py` (§11) —
  always finish a solver run with one authoritative `validate_layout` call.
