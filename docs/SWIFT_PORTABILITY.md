# PackPhysics — Linux portability / correctness audit

Swift 6.3.3 (swiftly), Ubuntu 26.04, Swift 5 language mode. Tests **178 passed / 0 failed** before and after
the fixes. `swift build -c release`, clean tree: **0 warnings** (library + `packphysics`).

## Fixed

1. **BUG — `validateLayout` could return a result `resultToJSON` cannot encode.** `sceneMetrics` divided the
   centroid by `total_mass_kg` unguarded (`Metrics.swift:50`), so any scene whose masses sum to zero (every
   `mass_kg: 0`, or masses that cancel) put `NaN` in `center_of_mass`/`com_offset_m`. Geometry is valid —
   `valid: true`, `score: 1.0` — but `JSONEncoder` rejects non-conforming floats, so `resultToJSON` threw
   `EncodingError.invalidValue … Path: metrics.com_offset_m[0]`. Python divides unguarded too, but
   `json.dumps` emits a bare `NaN` token: invalid JSON that no strict parser downstream (including this
   package's `resultFromJSON`) can read back. Both sides were broken; Swift merely failed at the producer.
   Fix: fall back to the unweighted centroid — the limit as all masses become equal.
2. **BUG — unvalidated `mass_kg`.** `precompute` validated non-finite dimensions and positions but not mass,
   so `NaN`/`inf` mass poisoned `total_mass_kg`, `center_of_mass` and the constraints load model
   (`supported_weight_kg`), again throwing at encode. Fix: one guard at the shared funnel
   (`SceneGeometry.swift:53`), same family as the existing non-finite checks → `MALFORMED_GEOMETRY`. Finite
   non-positive mass is still accepted (as in Python), now yielding a finite centroid.
3. **PORTABILITY — a Swift 6 iOS app could not call `validateLayout` off the main actor.** A Swift 5
   language-mode module grants its public types no implicit `Sendable` across the module boundary. Region
   isolation covers a locally-built `Scene`, but the realistic pattern — a `@MainActor` view model holding the
   scanned `Scene` and storing the result back — is a hard error: `type 'ValidationResult' does not conform to
   the 'Sendable' protocol`. The same gap stopped the library compiling in Swift 6 language mode
   (`Quat.identity`, `Mat3.identity`, `rigidityAllowanceFraction`, all `[#MutableGlobalVariable]`). Fix:
   explicit `Sendable` on the public value model — `Quat`, `Constraints`, `Rigidity`, `SceneObject`,
   `Container`, `Scene` (`Schema.swift`), `Mat3` (`Geometry.swift`), `JSONValue`, `ValidationResult`
   (`JSONValue.swift`). Additive conformances, no signature changed; the `@MainActor` pattern and a
   `swiftLanguageMode(.v6)` library build both compile and run.

## Fixed since this audit (owned elsewhere)

4. **BUG — `packphysics bench --objects -5` crashed.** `PackPhysicsCLI/main.swift:165`,
   `Int(ceil(sqrt(Double(n))))`: `n` came straight from `Int(args[i])`, so a negative count made `sqrt` NaN
   and the `Int(_:)` conversion **trapped** — `Fatal error: Double value cannot be converted to Int because it
   is either infinite or NaN`, exit 132, backtrace instead of the `die(…, code: 2)` path every other bad
   argument takes. Now fixed: the `--objects` parser guards `n >= 1` at `PackPhysicsCLI/main.swift:198`
   (`die("--objects requires a positive integer", code: 2)`). Only `Int(Double)` in the package; library code
   has none.

5. **Box-only port.** `swift/PackPhysics` now has convex-prism footprint parity with the Python
   validator (ff9673c; see `docs/SWIFT_PORT.md`, "Prism footprints"); the differential test that
   documented the divergence now asserts agreement.

## Verified clean

5. **No Apple-only APIs.** No `import simd`/`simd_*`/`CGFloat`/`CGPoint`/`Darwin`/`os.log`/`DispatchQueue`/
   `UIKit`/`NSString` bridging anywhere. `Vec3` is stdlib `SIMD3<Double>`, `pointwiseMin`/`pointwiseMax`
   stdlib, so the geometry needs no `simd` module. `Bundle.main`, `Process`, `FileManager`: only `CLITests`.
6. **Determinism across processes.** Swift seeds its hash per process, so same-process repetition proves
   nothing. A scene driving every dict-derived ordered output (`violated_walls`, `per_wall_depth_m`,
   `SOFT_COMPRESSION.walls`, constraint `details`, `metrics.per_object`; 4 violation + 3 warning types) gave
   **1 distinct SHA-256 over 25 separate process runs**. Every dict-sourced array is `.keys.sorted()` or from
   an ordered source; `Set` use is membership-only. `violated_walls` sorts lexicographically (`+x, +y, +z`),
   matching Python's `sorted(per_wall)`, not `_WALL_ORDER`. `.sortedKeys` makes bytes deterministic; JSON key
   order differs from Python's insertion order, but parity compares structurally, so that is cosmetic.
7. **No global mutable state.** Only `static let` / file-level `let` (lazily initialized via `swift_once`,
   thread-safe). Every module entry point is a pure function over value types, safe from any queue.
   `PlacementValidator` is a `final class` with `private var` state — per instance, so give each task its own.
8. **Foundation on Linux.** `.sortedKeys` works; `Bundle.module` loads the parity dataset and PAN sample
   (`ContractTests`/`IOTests` pass); `String(format:)` is locale-independent (identical under `LC_ALL=C` and
   `LC_ALL=de_DE.UTF-8`) and only printed, never fed to JSON. Glibc `hypot`/`acos`/`atan2` agree with
   CPython's (Neumaier `math.hypot` included) inside parity tolerance: 51 cases at 1e-9, all pass.
9. **n == 0 / n == 1.** Empty and one-object scenes validate and serialize cleanly:
   `aabbCandidatePairs`/`restingPairs` guard `n >= 2`, `sceneMetrics` has an `n == 0` branch, `pipeline`
   divides by `max(1, geom.n)`, and `Support.swift` switches to point counting below a 1e-9 m² footprint.

## Known, left alone

10. `Scene`/`Container`/`Constraints` collide with `SwiftUI.Scene` and other Apple names when an app imports
    both (`SceneObject` was already renamed for this). Qualify as `PackPhysics.Scene` in app code.
11. `Constraints.swift:229` derives `adjacent_heavy` from XZ overlap with no height test while the comment
    says "at roughly the same height" — faithful to `physics/constraints.py:238`, comment wrong in both.
    Fixing it breaks parity.
12. Public result types other than `ValidationResult` (`CollisionResult`, `ContainmentResult`,
    `SupportResult`, `ConstraintViolation`/`Warning`, `OBB`, `Placement`, `ScannedItem`, PAN shapes) are
    still non-`Sendable`. Annotate if the app starts sending them across actors.
13. `fill_ratio` can reach `NaN` for absurd-but-finite dimensions (~1e200 m). Not worth a guard.

## Timings (release, this box)

- `collideScene`, dense 20-box scene: **0.060 ms/call** (budget 0.5)
- `validateLayout` grid:n20:cell0.19: **0.450 ms/call** (Python ref ~2.1)
- `validateLayout` grid:n40:cell0.19: **1.166 ms/call** (Python ref ~3.0)
- `PlacementValidator.tryPlace` at k=20: **7.2 us/call**
