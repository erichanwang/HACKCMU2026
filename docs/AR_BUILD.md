# Building and running the AR app

Nobody on this team has compiled the iOS app. It was written on Linux against `swiftc -parse`
and the Linux test gate only (`scripts/ar_gate.sh`), which never touches ARKit/RealityKit/Xcode.
Every step below is unproven on a real device — read it as "should work from the code as it
reads today," not "we've done this."

## 1. Mac build

```sh
xcodegen generate
open Spike.xcodeproj
```

Then in Xcode: pick a **LiDAR device** as the run destination — a Pro iPhone or iPad
(`ARWorldTrackingConfiguration.sceneReconstruction = .mesh` in `Spike/ScanView.swift:36`
requires the LiDAR scanner). The Simulator cannot run this app at all: there is no ARKit camera
feed in the simulator, and `config.sceneReconstruction = .mesh` throws at runtime without LiDAR
hardware.

Note: `project.yml` sets `TARGETED_DEVICE_FAMILY: "1"` (iPhone only). An iPad Pro also has
LiDAR, but as configured today Xcode will not offer an iPad as a run destination — see the
project.yml note in the report below.

**Expect the first build to fail with type errors.** This code has never been through the
Swift compiler's full type checker (only `swiftc -parse` and, for the ARKit-free files,
`swiftc -Onone` builds under the Linux gate) — ARKit/RealityKit APIs, SwiftUI bindings, and
Codable conformances used in `Spike/ScanView.swift`, `Spike/SpikeApp.swift`, and `Spike/API.swift`
are all unverified.

## 2. Pointing the app at the server

`Spike/API.swift` no longer hard-codes a LAN IP. As it stands now:

- `API.defaultBase` (`Spike/API.swift:23`) reads the `PACKAR_SERVER` environment variable (set
  as an Xcode scheme variable — Product → Scheme → Edit Scheme → Run → Arguments →
  Environment Variables) and falls back to the literal `"http://172.26.48.172:8000"` if unset.
- `API.base` (`Spike/API.swift:25-31`) reads a `serverURL` string typed into the in-app Settings
  sheet (tap the URL button at the bottom of the screen, see `Spike/SpikeApp.swift:44` and
  `:52-59`), stored in `UserDefaults`/`@AppStorage`. If nothing has been typed or it doesn't
  parse to a URL with a host, it falls back to `defaultBase` above.
- An optional bearer token can also be typed in that same sheet (`authToken`,
  `Spike/SpikeApp.swift:23`); it's sent as `Authorization: Bearer ...` only when non-empty
  (`Spike/API.swift:37-38`) — needed because `server/auth.py` now gates mutating routes with
  Auth0 JWT auth.

Practical path for a teammate: on the Mac, `ipconfig getifaddr en0` to get its LAN IP; phone
and Mac must be on the same Wi-Fi network; type `http://<that-ip>:8000` into the app's Settings
sheet (no rebuild needed), or set `PACKAR_SERVER` in the Xcode scheme before building.

(This file is being edited concurrently with `Spike/API.swift`/`SpikeApp.swift` by another
agent — re-check both files if this section looks stale.)

## 3. Running the server

Mongo must be reachable before `uvicorn` even starts: `server/main.py:52-53` connects and pings
`db.client.admin.command("ping")` at import time, so the process exits immediately if Mongo
isn't up.

```sh
docker run --rm -d --name suitcase-mongo -p 27017:27017 mongo:7
cd server && uv run --env-file ../.env uvicorn main:app --host 0.0.0.0
```

Or set `SUITCASE_MONGODB_URI` (`server/main.py:52`) to point at an existing Mongo instead of
running a local container.

`.env` is not loaded automatically by anything in `server/` — `uv run --env-file ../.env` is
what loads it. Without `XAI_API_KEY` set (`server/main.py:86`), every item is labelled
"unknown" (Grok is never called). `.env` in this repo currently defines `PAN_API_KEY` and
`XAI_API_KEY`.

Binding `--host 0.0.0.0` (not the uvicorn default `127.0.0.1`) is what lets the phone, on the
same Wi-Fi network but a different device, reach the server at all.

## 4. What's proven on Linux vs. what needs the device

`bash scripts/ar_gate.sh` runs every `tests/swift/*/run.sh` (each sources
`swift/PackPhysics/swiftenv.sh` and builds with `swiftc -Onone`) plus any `scripts/ar_sim*.py`.
Today that covers:

- `tests/swift/scan/run.sh` — `Spike/Geometry.swift` scan geometry (`heightMap`, `fitBox`,
  `minAreaRect`, `connectedCluster`, `densify`) against synthetic point clouds, via a Linux
  simd shim (`tests/swift/SimdShim.swift`) — no ARKit involved.
- `tests/swift/plan/run.sh` — `Spike/PlanAnchor.swift`'s bag-frame-to-world-space math (pure
  arithmetic, deliberately has no ARKit/RealityKit import so it compiles under plain `swiftc`),
  checked against a real solver plan.

Neither of these touches ARKit, RealityKit, SwiftUI, or the network. What only a real device
can prove:

- ARKit mesh anchors actually appearing and being dense enough near a tapped object
  (`Spike/ScanView.swift`'s `ARMeshAnchor` walk).
- Horizontal plane detection finding the table under the suitcase.
- Raycast hit-testing behaving the way `view.raycast(from:allowing:alignment:)` is assumed to
  (`Spike/ScanView.swift:72`).
- RealityKit actually rendering the green scan-preview box and the coloured plan overlay boxes
  in the right place, at the right orientation, in AR space.
- `view.snapshot(...)` producing a real camera frame and the crop rect (`screenRect(of:in:)`)
  landing on the tapped object rather than off to one side.
- Real LiDAR noise, drift, and reflective/dark-surface dropouts — the synthetic point clouds in
  `tests/swift/scan/` are clean and noise-free by construction, which a real scan never is.

## 5. The demo loop

Scan suitcase → scan items → Pack → plan diagram → AR overlay. For each step, what "working"
looks like and its most likely failure — the failure modes below are `FIXES.md` section 3
("Risks that will bite in a real demo"), not re-derived here:

1. **Scan suitcase.** Tap the open, empty suitcase; status changes to "Creating suitcase…" then
   "Suitcase captured — switch to Item and tap what goes in." Most likely failure: the scan
   fits the bag's *outer* box (walls and lid thickness included), so the interior estimate is
   optimistic by a few centimetres unless the bag is scanned closed or a wall-thickness
   constant is right (FIXES.md 3, first bullet; `suitcaseWallMeters` in
   `Spike/ScanView.swift:18` is the calibration knob).
2. **Scan items.** Switch to Item mode, tap each object; status shows point/cell counts then
   "labelling…" while Grok runs. Most likely failure: Grok is synchronous inside `POST /items`
   and takes 5-6s per item (FIXES.md 3, third bullet) — five items is roughly half a minute of
   visible "labelling…" with no other feedback.
3. **Pack.** Tap Pack; button is disabled while a plan is in flight
   (`Spike/SpikeApp.swift:43`, `packing` state). Most likely failure: items pack as bounding
   boxes only — no nesting into gaps or open shoes (FIXES.md 3, second bullet) — and
   `POST /plan` itself is CPU-bound for ~3s under one uvicorn worker (FIXES.md 3, fourth
   bullet), so a second Pack tap while one is running would race it if the button weren't
   disabled.
4. **Plan diagram.** A sheet shows the 2D packing diagram; closing it returns to the AR view.
   Most likely failure: only the winning solver candidate's placements are stored, not the
   runner-up's, so there's no "here's the alternative" to fall back to if the top plan looks
   wrong (FIXES.md 3, sixth bullet).
5. **AR overlay.** Coloured boxes appear inside the scanned bag showing where each item goes
   (`Spike/ScanView.swift:201-224`, built from `Spike/PlanAnchor.swift`). Most likely failure:
   this step has literally never run on a device — the D2 math is tested on Linux
   (`tests/swift/plan/run.sh`) but the RealityKit rendering, anchor placement, and orientation
   quaternion are unverified outside a synthetic test.
