# packer3d ↔ physics ↔ PAN integration

`physics/packer3d_adapter.py` maps the solver's JSON (`packer3d/README.md`, "Output JSON
contract") into `physics.schema` scenes; `pan/solver_bridge.py` turns a `--compare` result into
PAN candidates. `packer3d/` itself is untouched.

## Frame mapping (shared spec — the Swift port implements the identical formulas)

packer3d: x = length (L), y = width (W), z = UP (H), origin at the container's MIN corner;
`placement.position` = MIN corner, `dims` = oriented bbox, `center = position + dims/2`.
physics: X = right, Y = UP, Z = forward, meters, container centred at `position`. The proper
rotation (det +1) sending packer z → physics Y and packer y → physics −Z is
`physics_point(x, y, z) = (x, z, -y)`. Hence:

* container → `Container(id, dimensions=(L, H, W), position=(L/2, H/2, -W/2))`: floor at Y = 0,
  min-x wall at X = 0, Z extent [−W, 0].
* placement → `Object(id=item_id, dimensions=(dx, dz, dy), position=(cx, cz, -cy),
  rotation=identity, mass_kg=mass)`, `(cx,cy,cz) = center`, `(dx,dy,dz) = dims` — exact for boxes,
  since packer3d only permutes axes and the oriented bbox stays axis-aligned.
* **orientation → quaternion** (`rotation_from_orientation`; used by
  `placements_from_packer3d` and every PAN action, *not* by `scene_from_packer3d`): when an
  object keeps its OWN unoriented dims and only the pose moves it, the axis permutation must
  travel in the rotation or the item lands 90° wrong. Permutation matrix `M[k, perm[k]] = 1`
  (`packer3d.models.BOX_ORIENTATIONS`; cylinder axes z/x/y → (0,1,2)/(2,1,0)/(0,2,1)); if
  `det M = −1` negate one column (a 180° flip, a box symmetry); then `R = P M Pᵀ` with `P` the
  matrix of `physics_point`. Tested: same world AABB as the oriented-dims mapping, to 1e-12.
* **constraints**: packer3d `fragile` ("nothing may rest on it") → `fragile=True,
  cannot_support_weight=True`. `keep_upright` lives in the scenario, not the placement, so every
  entry point takes `items=` (scenario dict or its `items` list) → `keep_upright=True`,
  `orientation_lock=None`; `priority` + the pre-`count` id land in `extras["items"]`.
* **cylinders** are packed by packer3d as their bbox and mapped as that box (conservative);
  `shape`/`radius`/`height`/`axis` survive in `extras["shapes"]` (`axis` is a PACKER axis letter:
  x→X, y→−Z, z→Y). **unpacked** items are not in the scene: `extras["unpacked"]`.
* **obstacles** → `obstacle:<id>`, `mass_kg=0.0`, rigid, so item↔obstacle collisions are caught.
  Massless scenes no longer divide by zero: `physics/metrics.py` falls back to the container centre
  for the COM at total mass 0 (the only physics edit made).
## API

```python
scene, extras = scene_from_packer3d(result, items=scenario, strategy="optimized")
placements    = placements_from_packer3d(result, strategy="naive")  # {"id","position","rotation"}
placement     = packer3d_placement_from_object(obj)                 # inverse, round trips to 1e-12
report        = validate_packer3d(result, items=scenario, strategy="naive")  # + report["adapter"]
before        = scene_from_packer3d_scenario(scenario)   # every item on the table, outside +X
```
A `--compare` result without `strategy=` raises `ValueError` naming the strategies.
`scene_from_packer3d_scenario` expands `count` via `packer3d.scenario.load_scenario` and rows the
items along +X: first near face at `X = L + 0.15`, step `length + 0.05`, on the floor, `Z = −W/2`,
dims `(length, height, depth)`. `candidates_from_packer3d(result, scenario)` → that scene plus one
`CandidateSequence` per strategy, placements in solver order, ids humanized into labels;
`first_divergence` is the first differing step, where `pan.demo.build_solver_state` starts the
rollout after applying the common prefix. CLI (exit: 0 valid, 1 violations, 2 bad input):

```bash
python3 -m physics validate-packer3d packer3d/examples/suitcase_result.json \
    --strategy optimized --items packer3d/examples/suitcase.json --pretty
PYTHONPATH=. python3 -m pan demo --from-packer3d packer3d/examples/suitcase_result.json \
    --scenario packer3d/examples/suitcase.json --backend mock --frames 6 --steps 2
```

## Agreement report

`packer3d.verify()` reports **no violations** on all four checked-in layouts. Ours, from
`validate_packer3d` (the stricter, authoritative check — never relaxed to agree):

| layout | ours | notes |
|---|---|---|
| suitcase naive (12 placed) | **clean**: 0 violations, 0 warnings | min support ratio 1.0, min stability margin +0.04 m (`wine`) |
| suitcase optimized (13) | **clean**: 0 violations, 0 warnings | min ratio 0.857 (`toiletries` on `books_1`), min margin +0.03 m (`umbrella`) |
| dragon naive (22) | 1 violation `UNSUPPORTED_OBJECT obstacle:hatch` | mapping artifact (1); clean with `include_obstacles=False` |
| dragon optimized (22) | same + warning `UNSTABLE_STACK crate_2` | support ratio 0.14, margin −0.12 m, on `CTB6` |

1. **`UNSUPPORTED_OBJECT obstacle:hatch` — mapping artifact, not a solver bug.** An obstacle is
   bolted to the hull, but `physics.schema` has no "static fixture" flag, so it becomes a free body
   floating at z = 0.95 with nothing beneath it. Use `include_obstacles=False` to judge only the
   items; keep them in to catch item↔obstacle collisions (tested).
2. **`UNSTABLE_STACK crate_2` — a real disagreement, of the expected class.** packer3d only
   requires support ratio ≥ `min_support` and never checks the COM projection; with `gravity=false`
   it skips support entirely. `crate_2` rests on 14 % of its base with its COM 0.12 m outside the
   support polygon — under gravity it topples. Correct per the solver's spec, flagged by ours,
   harmless in microgravity (strap it anyway).
3. **The expected suitcase `UNSTABLE_STACK` / `FRAGILE_LOAD` disagreements did not occur.**
   `min_support = 0.6` leaves room for a COM-outside-support stack, but both layouts happen to keep
   every stacked item's COM inside its support polygon (worst margin +0.03 m) and never put
   anything above a `fragile` item. Zero disagreements — no solver bug, no mapping bug.
4. **Not evidence of agreement:** the Dragon shows 0 `CONTAINER_PENETRATION`, but a cylindrical
   container is mapped to its bbox `(2R, 2R, H)`, so our containment is *weaker* there and cannot
   see a curved-wall violation — `packer3d.verify()` is authoritative for it. (Microgravity is
   likewise not representable: `physics.schema` always pulls along −Y.) **Mapping cross-check:**
   our mass-weighted COM equals `physics_point(*metrics["com"])` to 1e-9 on every layout.
