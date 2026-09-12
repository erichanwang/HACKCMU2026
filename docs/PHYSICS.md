# Physics validation layer — technical reference

Validates a suitcase-packing layout: are objects inside the container, not
overlapping, adequately supported, and consistent with travel-specific
constraints (fragile, keep upright, etc.)? Eight modules, each independently
tested; `physics/validator.py` composes them into one API (§8).

## 1. Units and coordinates

- **Units**: meters, everywhere (mass in kg).
- **Axes**: X = right, Y = up, Z = forward. Right-handed.
- **Quaternion**: `(x, y, z, w)`, unit-length, world orientation of the
  object's local axes. Identity = `(0, 0, 0, 1)`.
- **Dimensions**: `(length, width, height)` — full extents (not half-extents)
  along local x/y/z, measured *before* rotation is applied.
- **IDs**: stable strings, unique within a `Scene` (including no collision
  with the container's own id) — enforced by
  `containment.check_no_duplicate_ids`.

Schema (`physics/schema.py`): `Object` (id, dimensions, position, rotation,
mass_kg, constraints), `Container` (id, dimensions, position, rotation),
`Scene` (container, objects), `Constraints` (fragile, keep_upright,
cannot_support_weight, heavy, orientation_lock).

## 2. Geometry representation

Every object and the container is represented as an **OBB** (oriented
bounding box) — a center, 3 orthonormal world-space axes, 3 half-extents —
built exclusively through `physics.geometry.obb_from()` so every module
agrees on one rotation convention.

**Why OBB, not AABB or full mesh:**
- AABB (axis-aligned) throws away orientation — a shoe packed diagonally or
  a laptop stood on its edge would be tested against its axis-aligned
  bounding box, which is wrong for both containment and stacking.
- Full mesh collision is the "correct" answer but is overkill for a 24-hour
  hackathon: it's slower per-pair and far more implementation risk, for
  accuracy gains that don't matter when the packable items are themselves
  approximated as boxes.
- OBB is the coarse-grained sweet spot: orientation-aware, O(1) per test,
  numerically simple (SAT reduces to 15 dot products for box-box).

**Coarse-to-fine story**: OBB-OBB is the *only* narrow-phase implemented
today. Convex-hull / mesh narrow-phase is documented future work in
`collision.py`'s docstring, not implemented — do not assume finer-grained
shape checks exist.

**Core functions** (`physics/geometry.py`):
- `quat_to_matrix(q)` — `(x,y,z,w)` → 3x3 rotation matrix; raises `ValueError`
  on a zero/non-finite-norm quaternion, defensively re-normalizes if norm is
  off by more than `1e-3` (typo-level drift) rather than silently producing a
  skewed matrix.
- `obb_from(entity)` — validates `dimensions` (finite, >0) and `position`
  (finite, shape `(3,)`), returns an `OBB(center, axes, half_extents, id)`.
  Both `Object` and `Container` use it.
- `obb_vertices(obb)` — the 8 world-space corner points, via the sign-pattern
  of `±half_extent` offsets rotated into world space.

## 3. Collision detection (SAT)

`physics/collision.py`: **Separating Axis Theorem** for two convex OBBs. Two
boxes are separated iff some axis exists along which their projections don't
overlap. For box-box, the candidate axes are exactly 15: 3 face normals of A,
3 face normals of B (= each box's own local axes), and the 9 pairwise cross
products of A's edges with B's edges. Checked in a fixed order (A's 3, B's 3,
then the 9 cross products) — order matters for which axis wins ties in the
minimum-overlap search.

**Epsilon semantics** (precise — read before touching thresholds): `epsilon`
is a distance-space slack on the *overlap along the current axis*, not on
the collision decision as a whole. Per axis, `overlap = radius_sum -
center_dist`. If `overlap <= epsilon` on *any* axis, the boxes are **not**
colliding — separated, touching, or overlapping by less than epsilon all
count as separated. Consequence: two boxes touching exactly report
`colliding=False`, `penetration_depth_m=0.0`, for any `epsilon >= 0`. If
every one of the 15 axes has `overlap > epsilon`, they collide, and
`penetration_depth_m` is the *minimum* such overlap (MTV magnitude), `axis`
its direction (oriented A→B). Default `epsilon=1e-6`: a razor-thin true
overlap (1e-4 m) still reports colliding; float-error "touching" (~1e-9 m)
correctly reports not colliding.

**Broad phase**: `aabb_overlap(a, b)` — computes each OBB's world AABB from
its 8 vertices and does 3 interval-overlap checks. Necessary-but-not-
sufficient (never a false negative, can be a false positive for two rotated
boxes whose tight AABBs overlap but the boxes themselves don't) — cheap early
exit before the full 15-axis SAT. `check_collision` runs this itself
internally (epsilon-aware margin) before doing full SAT, so callers get the
speedup for free.

**Complexity**: `check_collision` is O(1) — 15 fixed axes, each O(1) (project
half-extents via `|axis·column| * half_extent`, not the 8 vertices
explicitly). Over n objects, all-pairs collision is O(n²); no spatial index
in this module — a caller needing one adds a grid/BVH in front.

**Known failure modes** (from the module docstring):
- Boxes only — concave/non-box shapes aren't modeled; the OBB may report a
  collision the true mesh wouldn't have (or vice versa).
- Near-parallel edges — a cross-product axis for two nearly-parallel edges
  has near-zero magnitude and is skipped rather than normalized (avoids
  divide-by-tiny-number). This is the classic SAT edge case: two boxes
  separated *only* by that one degenerate axis can rarely be misreported as
  colliding.
- Large coordinate magnitudes — float64 has ~15-17 significant digits;
  precision degrades at coordinates ~1e6 m. Not addressed; fine for
  suitcase-scale scenes (order 1 m).
- `contact_point` is a documented approximation (midpoint of the overlap
  interval along the MTV axis), not a true contact manifold/polygon.

## 4. Containment

`physics/containment.py`: is an object OBB fully inside the container OBB?
The container may itself be rotated/positioned arbitrarily — rotation is
never assumed identity.

**Correct-for-rotation check**: all 8 world-space vertices of the object are
projected into the *container's local frame* (`(verts - container.center) @
container.axes`) and compared against the container's half-extents on each
local axis. Checking only the object's center is insufficient — a small
rotation can poke a corner through a wall while the center stays well
inside.

`epsilon` (default `1e-6` m) is a wall tolerance: a vertex up to `epsilon`
beyond a wall still counts as contained/touching (absorbs float noise, lets
flush-against-the-wall packing validate). Beyond epsilon it's a penetration.

`ContainmentResult`: `object_id`, `contained` (bool), `penetrating_vertices`
(world-space points that failed), `penetration_depth_m` (max overshoot over
all vertices/axes), `violated_walls` (sorted subset of `{-x,+x,-y,+y,-z,+z}`
in container-local axes).

`check_scene_containment(scene)` calls `check_no_duplicate_ids` first, then
returns **only the violating results** — a filtered list, empty means fully
contained.

**Complexity**: O(1) per object (8 vertices × 3 axes), O(n) per scene.

**Limitations**: object-vs-container only — object-vs-object overlap is
`collision.py`'s job, not this module's. Assumes convex box geometry, and is
itself rigid-only — soft/compressible items are handled one layer up, in
`validator.py` via `physics/compressibility.py` (§7), not inside this module.

## 5. Support and static stability

`physics/support.py` — **a static stability heuristic, not a full rigid-body
simulator.** No force/torque integration over time, no friction, no dynamic
tipping simulation. It answers only: "given these final resting poses, does
each object look adequately supported and balanced under gravity alone?"

**Model**:
- Rigid bodies, uniform density per object ⇒ center of mass = OBB center.
- Gravity along -Y.
- Footprint approximation (stated limitation, not hidden): an object's
  bottom/top contact footprint = the XZ-plane axis-aligned bounding
  rectangle of its 4 lowest-Y (or highest-Y) OBB vertices. **Exact only for
  yaw-only rotation** (pure rotation about world Y, or none); for roll/pitch
  this over-estimates true footprint area, since the real contact patch is a
  rotated quadrilateral, not its bounding box. Accepted for hackathon scope
  because upstream validity (non-colliding, contained) tends to keep packed
  objects axis-aligned or yaw-only in practice.
- Container floor: single flat plane at the container OBB's minimum world-Y;
  its footprint is the XZ bounding rectangle of its own 4 lowest-Y vertices.
- Multi-support union area (floor + neighboring objects) computed by exact
  coordinate-compression rasterization of axis-aligned rectangles — no
  Monte Carlo, no sampling error, but still bounded by the footprint
  approximation above.

**Outputs** (`SupportResult`): `support_ratio` = covered footprint area /
total bottom footprint area, clamped to 1.0. `stability_margin_m` = signed 2D
distance from the COM's (X,Z) to the nearest edge of the *merged bounding
rectangle* of all contributing support regions — positive = COM inside,
negative = outside/overhanging. If `support_ratio == 0` (no support at all),
there's no rectangle to measure against, so `stability_margin_m` is the
sentinel `FLOATING_MARGIN_SENTINEL_M = -1.0e6`, not a real distance.
`floating` = `support_ratio < floating_threshold` (default 0.05). `unstable`
= `stability_margin_m < 0`. `supporting_objects` lists ids (or
`"container_floor"`).

**Complexity**: O(n²) worst case (every object checked against every other
object plus the floor). Fine at hackathon scale (5-20 objects); would need a
spatial index beyond that.

**Limitations**: no friction (an object obviously wall/neighbor-braced by
friction is judged on footprint/COM alone), no dynamic tipping, multi-point
support of tilted stacks is over-approximated via bounding rectangles, no
chain-reaction modeling (A destabilizing B destabilizing C) — each object
evaluated independently.

## 6. Travel constraint layer

`physics/constraints.py` — optional, opt-in metadata layer. **No-op when
every object's `Constraints` is left at defaults** (all `False` / `None`);
only flags what an object explicitly opts into.

| Constraint field | Check | Result type | Kind |
|---|---|---|---|
| `keep_upright` | local Y axis tilt from world up > `angle_tol_deg` (default 15°) | `LIQUID_NOT_UPRIGHT` | violation |
| `orientation_lock="this_side_up"` | same as `keep_upright` | `INVALID_ORIENTATION` | violation |
| `orientation_lock="flat_only"` | local Y axis not within tolerance of ±world-Y (lying on its largest face) | `INVALID_ORIENTATION` | violation |
| `orientation_lock="horizontal"` | local Y axis not within tolerance of the XZ plane | `INVALID_ORIENTATION` | violation |
| `cannot_support_weight` | nonzero mass resting on top (weight-overlap, below) | `FRAGILE_OBJECT_OVERLOADED` | violation |
| `fragile` | mass resting on top, OR footprint overlaps a `heavy` object's footprint at ~same height | `FRAGILE_LOAD` | warning |
| `heavy` | anything resting on top of it | `HEAVY_ON_TOP` | warning (always emitted when true, independent of what's underneath) |

**"Resting on" heuristic**: ordered pair (X, Y), Y rests on X if Y's OBB
lowest-Y extent is within `contact_eps_m` (default 0.02 m) of X's highest-Y
extent AND their XZ axis-aligned bounding rectangles (from each OBB's 8
vertices — already rotation-aware) overlap by any nonzero amount. O(n²) over
scene objects. The `fragile`/`heavy` "adjacent" check reuses the same
XZ-rectangle-overlap test *without* the height requirement — a
footprint-proximity approximation, not true 3D side-contact.

## 7. Compressibility (soft/semi-rigid items)

`physics/compressibility.py` — layered on top of `collision.py`/`containment.py`
inside `validator.py`, not inside those modules themselves (they stay rigid-box-only).
Two new `Object` fields drive it: `rigidity` (`"rigid"` | `"semi"` | `"soft"`,
default `"rigid"`) and `compressibility_k` (`>= 1.0`, default `1.0`, ignored when
`rigidity="rigid"`). `dimensions` always stays the object's *loose* (uncompressed)
size, per OVERVIEW.md's packing model — the compression allowance below is how
much of that loose size can plausibly squish into an occupied void, not a
resized OBB.

**Heuristic** (explicitly crude — a single scalar per axis, not a deformation
model; real fabric/foam compression is anisotropic and load-dependent):

```
compression_allowance_m(obj, extent_m) =
    0                                                  if rigid
    clamp(extent_m * (1 - 1/k) * fraction, 0, 0.95*extent_m)   otherwise
```
where `fraction = RIGIDITY_ALLOWANCE_FRACTION[rigidity]` = `{semi: 0.5, soft: 1.0}`
and `extent_m` is the object's own full extent along whichever axis is being
checked (a collision MTV axis, or its own extent projected onto a violated
container wall's axis). The 95% cap prevents an object from ever being treated
as compressing its entire size away. A rigid object always gets zero allowance
regardless of `compressibility_k` — k is meaningless for something that never
compresses.

**Where it plugs into `validate_layout`**: for a colliding pair, the combined
allowance (`combined_collision_allowance_m`, each object's own allowance summed)
is subtracted from the raw SAT `penetration_depth_m` before deciding
violation-vs-not. If the allowance fully absorbs the overlap, the pair is
downgraded from a hard `OBJECT_COLLISION` violation to a `SOFT_COMPRESSION`
warning (`{"type": "SOFT_COMPRESSION", "objects": [...], "raw_penetration_depth_m",
"compressed_depth_m"}`); if overlap remains past what compression can plausibly
absorb, it's still a violation, but severity is computed from the *excess*, not
the raw depth. The same policy applies to container-wall penetration
(`CONTAINER_PENETRATION` → `SOFT_COMPRESSION` warning with `"object"` instead of
`"objects"`), using the object's own extent projected onto the violated wall's
axis (there's no per-wall depth breakdown from `containment.py`, so the
first-in-sorted-order violated wall stands in for the axis; the true overall
raw depth is still what gets reduced).

Rigid-only scenes are byte-for-byte unaffected (allowance is always exactly
`0.0`), so this is purely additive to the original rigid-object pipeline.

## 8. `validate_layout()` API

`physics/validator.py` has landed; this section documents its actual
behavior (read from source, not the generic contract).

```python
def validate_layout(scene: Scene) -> dict:
    """
    {
      "valid": bool,       # True iff violations is empty
      "score": float,      # 1.0 clean, lower as violations pile up/worsen
      "violations": [...], # hard failures
      "warnings": [...],   # soft/informational, do not affect valid/score
    }
    """
```

**Malformed-input guard**: before running anything, validates up front via
`check_no_duplicate_ids` + `obb_from` on the container and every object. Any
`ValueError` (NaN/non-finite dims or position, bad quaternion, duplicate
ids) is caught and short-circuits to `{"valid": False, "score": 0.0,
"violations": [{"type": "MALFORMED_GEOMETRY", "object": ..., "detail": ...}],
"warnings": []}` — the rest of the pipeline never runs on untrustworthy
data. Function **never raises**.

**Pipeline order**: containment → collision (all pairs, `aabb_overlap`
broad-phase then `check_collision` SAT) → support → constraints.

**Violation/warning type table**:

| Type | Meaning | Produced by | Hard-fail or warning |
|---|---|---|---|
| `MALFORMED_GEOMETRY` | invalid geometry/duplicate ids caught before the pipeline runs | `validator.py` guard | violation (short-circuits, score forced 0.0) |
| `CONTAINER_PENETRATION` | object vertex outside container walls beyond epsilon | `containment.check_scene_containment` | violation |
| `OBJECT_COLLISION` | two OBBs overlap beyond `epsilon` (SAT) | `collision.check_collision` | violation |
| `UNSUPPORTED_OBJECT` | `SupportResult.floating` (`support_ratio < floating_threshold`, default 0.05) | `support.check_support` | violation |
| `UNSTABLE_STACK` | supported (`support_ratio >= floating_threshold`) but `SupportResult.unstable` (COM outside support rectangle) | `support.check_support` | **warning** |
| `LIQUID_NOT_UPRIGHT` | `keep_upright` object tilted past tolerance | `constraints.check_constraints` | violation |
| `INVALID_ORIENTATION` | `orientation_lock` violated | `constraints.check_constraints` | violation |
| `FRAGILE_OBJECT_OVERLOADED` | weight resting on a `cannot_support_weight` object | `constraints.check_constraints` | violation |
| `FRAGILE_LOAD` | `fragile` object loaded or adjacent to `heavy` | `constraints.check_constraints` | warning |
| `HEAVY_ON_TOP` | anything resting on a `heavy` object | `constraints.check_constraints` | warning |

**Hard-fail vs warning policy**: collisions and container-wall penetrations
are always hard fails (physically impossible geometry). A fully unsupported
object (`UNSUPPORTED_OBJECT`) is a hard fail. An object that *is* supported
but off-balance (`UNSTABLE_STACK`) is only a warning — precarious, not
impossible; `valid` can still be `True`. This is deliberate (no
friction/tip-over dynamics modeled — see §5); treat repeated
`UNSTABLE_STACK` warnings as "worth a second look," not "reject."

**Score formula**: `score = max(0, 1 - sum(min(1, severity) for v in
violations) / max(1, n))` where `n = len(scene.objects)`. Warnings never
affect score. Per-type severity (each clamped to `[0, 1]`):

| Type | Severity formula |
|---|---|
| `OBJECT_COLLISION` | `penetration_depth_m / min(smallest full extent of A, smallest full extent of B)` |
| `CONTAINER_PENETRATION` | `penetration_depth_m / smallest container half-extent` |
| `UNSUPPORTED_OBJECT` | `1 - support_ratio / floating_threshold` |
| `FRAGILE_OBJECT_OVERLOADED` | `supported_weight_kg / max(0.1, object's own mass_kg)` |
| `LIQUID_NOT_UPRIGHT` / `INVALID_ORIENTATION` | `(tilt_deg - tolerance_deg) / (90 - tolerance_deg)` |
| `MALFORMED_GEOMETRY` | none — short-circuits, score forced to 0.0 |

Score is monotonic: adding a violation or increasing any severity can only
lower or hold the score.

**Determinism**: no randomness; `violations` and `warnings` are each sorted
by `(type, object_id)` (for `OBJECT_COLLISION`, the pair's
lexicographically-smaller id) before being returned — repeated calls on the
same scene produce byte-identical `json.dumps` output.

**Example shape** (`OBJECT_COLLISION` violation):
```python
{"type": "OBJECT_COLLISION", "objects": ["laptop", "shoe"],
 "penetration_depth_m": 0.012, "contact_point": [0.1, 0.3, -0.2],
 "severity": 0.4}
```

## 9. Performance

**Complexity**: O(n²) overall, dominated by the all-pairs collision scan —
each pair pruned first by O(1) `aabb_overlap`, only survivors run full O(1)
SAT, so the O(n²) factor is cheap broad-phase work per pair, not O(n²) SAT.
`check_scene_containment` is O(n); `check_support` and `check_constraints`
are each O(n²) internally (same scale as the collision scan). Fine at
hackathon scale (5-20 objects); a spatial index (grid/BVH) is the upgrade
path beyond that.

**Benchmark** (`python3 tests/benchmark_validator.py`, non-colliding grid
scenes, this machine, single run):

| Objects | Time |
|---|---|
| 5 | 2.1 ms |
| 10 | 3.4 ms |
| 20 | 9.1 ms |

Roughly consistent with O(n²) growth (2.1 → 3.4 → 9.1 ms as n doubles twice);
well within interactive/re-check budget for a packing solver's inner loop.

## 10. Known limitations (consolidated)

- **Narrow-phase is OBB-only** — no convex-hull or mesh narrow-phase;
  documented future work in `collision.py`, not implemented.
- **No friction model** anywhere (collision, containment, or support) — an
  object braced by friction is judged purely on geometry/footprint.
- **No dynamic simulation** — no forces/torques integrated over time, no
  tipping under acceleration, vibration, braking, or drops. Support is a
  static, single-instant heuristic.
- **PyBullet (or similar) full rigid-body final-check is a documented future
  option** per the project's PRD/MVP docs — not implemented anywhere in this
  layer today.
- **Tilted-object footprint over-estimation** in `support.py`: bounding-
  rectangle approximation is exact only for yaw-only rotation; roll/pitch
  over-estimates contact area.
- **SAT near-parallel-edge edge case** in `collision.py`: boxes separated
  only by a degenerate (near-zero-norm) cross-product axis can rarely be
  misreported as colliding.
- **Float precision at large coordinates** (~1e6 m) is not addressed;
  assumes scene coordinates near the origin (true for suitcase-scale
  scenes).
- **No multi-body chain-reaction modeling** in support — each object's
  support is evaluated independently, so A destabilizing B destabilizing C
  isn't captured.
- **Containment and support both assume convex box geometry**, no non-box
  shapes. Compressibility (§7) is layered on top in `validator.py`, using a
  single crude per-axis allowance scalar — not a real deformation model, and
  not something `collision.py`/`containment.py`/`support.py` know about
  themselves.
- **`contact_point` in collision.py** is an approximate single point, not a
  true contact manifold/polygon.
- **`UNSTABLE_STACK` is a warning, not a hard fail** — a scene with
  precariously balanced (but supported) objects is still `valid: True`; no
  friction/tip-over dynamics back this judgment (see §5).
