# PackPhysics

On-device Swift port of the Python `physics/` validation layer (HackCMU
2026 suitcase packer): same units, axes, quaternion order, JSON keys, and
violation/warning/score contract, so a scene validated in the iOS app
matches what the Python prototype would say. Checked against the Python v2
validator via a 51-scene parity dataset (`Tests/PackPhysicsTests/Resources/parity_v2.json`)
rather than by re-reading the physics docs — see `docs/SWIFT_PORT.md` at the
repo root for how that comparison works and what it found.

## Add to the iOS app

**xcodegen (`project.yml`)** — add a local package and depend on it from the
app target (someone with edit rights to `project.yml` should add this; this
package does not touch it):

```yaml
packages:
  PackPhysics:
    path: swift/PackPhysics

targets:
  Spike:
    dependencies:
      - package: PackPhysics
```

**Xcode only, no xcodegen**: File → Add Package Dependencies… → Add Local…,
select `swift/PackPhysics`.

## From LiDAR scan to validation

```swift
import PackPhysics

// BoxFit (metres, yaw-only pose) from the scan spike -> SceneObject:
let shoe = sceneObject(id: "shoe", widthM: 0.30, depthM: 0.12, heightM: 0.11,
                       center: boxFit.center, axis: boxFit.axis)  // SIMD3<Float> overload

// ScannedItem (centimetres, no pose yet) -> SceneObject, placeholder pose:
let laptop = sceneObject(from: scannedItem)

let container = Container(id: "carry_on", dimensions: Vec3(0.56, 0.23, 0.36),
                          position: Vec3(0, 0.115, 0))
let scene = Scene(container: container, objects: [shoe, laptop])

let result = validateLayout(scene)
if !result.valid {
    for v in result.violations {
        let type = v["type"]?.stringValue ?? "?"
        let objects = v["objects"]?.arrayValue?.compactMap(\.stringValue)
            ?? [v["object"]?.stringValue].compactMap { $0 }
        let depth = v["penetration_depth_m"]?.doubleValue
        let contact = v["contact_point"]?.arrayValue?.compactMap(\.doubleValue)
        print(type, objects, depth ?? "-", contact ?? "-")
    }
}
```

`validateLayout` never throws — malformed input (bad quaternion, duplicate
ids, non-finite dims, ...) comes back as a `MALFORMED_GEOMETRY` violation
inside a normal (invalid) `ValidationResult`, exactly like the Python
`validate_layout`.

**Solver inner loop** — don't call `validateLayout` per candidate placement
while searching (it revalidates the whole scene, every check, every call);
use the incremental validator, which only checks a new placement against
what's already committed:

```swift
let pv = try PlacementValidator(container: container)  // throws on a malformed container
for obj in objectsInSolverOrder {
    let check = pv.tryPlace(obj)
    if check.valid {
        pv.place(obj)
    } else {
        // try a different pose for `obj`
    }
}
let finalResult = validateLayout(pv.toScene())  // one authoritative pass at the end
```

## JSON interop

- `sceneFromJSON(_:) throws -> Scene` / `sceneToJSON(_:pretty:) throws -> Data` — round-trip a whole `Scene`, same JSON shape as `physics/io.py`.
- `placementsFromJSON(_:) throws -> [Placement]` — accepts the solver's `{"placements":[...]}` wrapper or a bare array, and either `position`/`rotation` or `target_position`/`target_rotation` per entry.
- `resultToJSON(_:pretty:) throws -> Data` / `resultFromJSON(_:) throws -> ValidationResult`.
- `panCandidates(from:) throws -> PANCandidatesFile` — decodes the PAN layer's `candidates.json` (see `Tests/PackPhysicsTests/Resources/pan_candidates_sample.json` for a real example). Pure data modeling; no PAN logic lives in this package.

## Building and testing on Linux

```sh
source swift/PackPhysics/swiftenv.sh && swift test
```

This repo's CI runs on Ubuntu with a user-space `swiftly` toolchain;
`swiftenv.sh` puts `swiftly`'s `swift` on `PATH` and prepends a private
libxml2 compat lib dir, because the toolchain (built against Ubuntu 24.04)
needs `libxml2.so.2`, which this Ubuntu 26.04 box doesn't ship. On
macOS/Xcode none of this is needed — just build/run/test normally.

## Numbers

Collision is the one module with a measured perf comparison against Python.
On a dense 20-object scene (0.2 m boxes on a 0.19 m grid + jitter, ~100 of
190 pairs overlapping):

- Swift, release, measured on this machine (`swift test -c release --filter CollisionTests`):

  ```
  collideScene, dense 20-box scene: 0.029407100000000002 ms/call over 50 calls (budget 0.5 ms)
  ```

  (`CollisionTests.swift`'s own comment reports 0.041 ms/call release / 1.44 ms/call debug from its author's machine — same ballpark.)
- Python (`physics/collision.py` module docstring): `collide_scene` on the
  same shape of scene is "~1.1 ms per call".

So Swift release is roughly 25-35x faster than the Python figure here —
expected for a compiled per-pair loop vs. numpy-batched Python, not a claim
every module was benchmarked this way.

## Linux

Three ways to build and test this repo (Python + this package) on Linux,
from the repo root:

1. **User-space, any Ubuntu (22.04/24.04/26.04+):**
   ```sh
   make setup-linux && make test-swift
   ```
   `setup-linux` installs `swiftly` and the latest stable toolchain into
   `~/.local/share/swiftly` if needed (no sudo), and only on a system whose
   toolchain is missing `libxml2.so.2`/`libicuuc.so.74` (e.g. 26.04), fetches
   the Ubuntu noble `.deb`s and extracts them into `~/.local/swift-compat`.
   Safe to re-run.
2. **Clean room, zero host setup:**
   ```sh
   make docker-test
   ```
   Builds the repo-root `Dockerfile` (`swift:*-noble`, so no compat step is
   needed) and runs `python3 -m unittest discover` then `swift test` inside.
3. **CI:** `.github/workflows/ci.yml` runs on every push/PR — a `python` job
   (Ubuntu, Python 3.12) runs the physics unit tests and the PAN mock demo,
   uploading its summary/comparison image as artifacts; a `swift` job
   (Ubuntu 24.04, `swift-actions/setup-swift@v2`) runs `swift build -c
   release` and `swift test` for this package, with `.build` cached.
