# The Swift port

## Why a port, not a rewrite

The AR flow needs to validate a packing layout on-device, live, as the user
scans and the solver searches poses — there's no Python on iPhone, and a
network round-trip per candidate placement isn't an option for an
interactive solver loop. So `swift/PackPhysics` re-implements the validation
math (`physics/*.py`) in Swift rather than wrapping it.

It is a *faithful* port on purpose: one contract (units, axes, quaternion
convention, JSON keys, violation/warning types, score/severity formulas),
two runtimes. A rewrite that reinterpreted the physics would drift from the
Python reference the moment nobody was looking; a faithful port can instead
be checked against it mechanically — see Parity below — so "does the app
agree with the prototype" is a test result, not a code review question.

## Module map

| Python | Swift | Tests |
|---|---|---|
| `physics/schema.py` | `Schema.swift` | via `ContractTests` + `IOTests` round-trips |
| `physics/geometry.py` | `Geometry.swift` | via `ContractTests` (quaternion convention) + every other module (all build OBBs through it) |
| `physics/scene_geometry.py` | `SceneGeometry.swift` | via `ContractTests` (`precompute`, malformed-input naming) |
| `physics/collision.py` | `Collision.swift` | `CollisionTests.swift`, 21 |
| `physics/containment.py` | `Containment.swift` | `ContainmentTests.swift`, 19 |
| `physics/compressibility.py` | `Compressibility.swift` | `CompressibilityTests.swift`, 8 |
| `physics/support.py` | `Support.swift` | `SupportTests.swift`, 17 |
| `physics/constraints.py` | `Constraints.swift` | `ConstraintsTests.swift`, 17 |
| `physics/metrics.py` | `Metrics.swift` | `MetricsTests.swift`, 12 |
| `physics/io.py` | `IO.swift` | `IOTests.swift`, 20 |
| `physics/validator.py` | `Validator.swift` | `ValidatorTests.swift`, 17 |
| `physics/incremental.py` | `Incremental.swift` | `IncrementalTests.swift`, 17 |
| — (Swift-only: shared JSON value + `ValidationResult` shape) | `JSONValue.swift` | exercised by every module's tests |

`ContractTests.swift` (4 tests) checks cross-module glue: the parity dataset
loads, the quaternion convention matches Python's, `precompute` numbers
match a hand-checked fixture, and malformed scenes name the right object.
178 tests total, all passing (`swift test`, this checkout).

`Validator.swift`/`Incremental.swift` landed while this doc was being
written; signatures as built: `validateLayout(_ scene: Scene,
floatingThreshold: Double = 0.05) -> ValidationResult` (never throws) and
`validate(_ scene: Scene, placements: [Placement]? = nil) ->
ValidationResult` match the contract exactly. `PlacementValidator` also
matches (`tryPlace`, `place(_:force:)`, `remove(id:)`, `toScene()`,
`placedIds`, `incrementalMetrics`) with one addition worth flagging: its
initializer **throws** —
`init(container:epsilon:contactEps:floatingThreshold:) throws` (a malformed
container raises `MalformedSceneError`, same as Python's `obb_from` from
`__init__`) — the contract this doc started from didn't call that out.

## The parity method

`Tests/PackPhysicsTests/Resources/parity_v2.json` holds 51 scenes, each
paired with the Python v2 validator's output on that exact scene: 11 named
fixtures from `tests/fixtures.py` (hand-built, hand-checked geometry), 9
grid scenes (n=5/20/40 objects on a 0.3/0.2/0.19 m cell — sparse, dense, and
touching-density packings), 30 seeded random scenes (random dimensions,
mass, rigidity/compressibility, constraints, and rotations that include
roll/pitch, not just yaw), and 1 deliberately malformed scene (duplicate
ids). Loaded once per test run via `Bundle.module` (`ContractTests.Parity`).

What's compared: structure (does Swift raise/flag the same objects) plus
numeric fields to a 1e-9 tolerance (1e-12 for the SAT depth spot-check
below) — `assertJSONClose`/`assertConstraintsDetailClose` walk the JSON
dictionaries recursively rather than requiring exact key-for-key string
equality, so extra Swift-side fields don't fail a comparison.

What's deliberately not compared: malformed-input error message text (only
that a `MalformedSceneError` names the right offending id).

Reported results per module, from what the tests actually assert:
- **Metrics** — `sceneMetrics` matches the Python `metrics` dict structurally to 1e-9, across all 51 parity scenes (`MetricsTests.testDatasetParityMetrics`).
- **Constraints** — `checkConstraints` matches Python's violations/warnings the same way, across all 51 scenes, skipping the malformed one (`ConstraintsTests.testDatasetParityAgainstPythonValidator`).
- **Containment** — matched to 1e-9 (depth, per-wall depth, violated walls) against 2 named fixtures (`scene_with_wall_penetration`, `scene_oversized_object`), not the full 51-scene set.
- **Collision** — SAT depth matched to 1e-12 against 100 seeded-random OBB pairs, replayed from the same LCG in Python (`CollisionTests.test100RandomPairsMatchPythonAndAreSymmetric`) — a separate dataset from `parity_v2.json`.
- **Support** — checked against 3 named fixtures with hand-verified/Python-derived numbers to 1e-9 ("to the bit" per the test's own comment), not a dataset-wide loop over all 51 scenes.

## Known differences

- **`Float` vs `Double` at the ARKit boundary**: the LiDAR spike
  (`Spike/Geometry.swift`, `BoxFit`/`ScannedItem`) measures in `Float` (ARKit's
  native type); this package computes entirely in `Double` (matching
  Python's float64). `IO.swift` converts once, at the adapter boundary.
- **No numpy batching**: the Python layer vectorizes per-scene checks over
  numpy arrays; Swift just loops per object/pair. Fine at packing scale
  (n <= ~40) once compiled — see the README's collision numbers.
- **Linux toolchain**: this repo's CI runs a user-space `swiftly` toolchain
  on Ubuntu, which needs a compat `libxml2` the OS doesn't ship; `swiftenv.sh`
  papers over it. Not a concern on macOS/Xcode.

## What's not ported

The PAN world-model pipeline (`pan/`, simulation rollouts, risk scoring)
stays server-side in Python — it's a video-model-backed simulator, not
something that runs on-device. The app only consumes its output,
`candidates.json`, via `panCandidates(from:)` in `IO.swift`.
