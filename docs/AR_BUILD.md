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

## 6. Calibrating the suitcase interior model

`tests/swift/bag/run.sh` measured (on three simulated carry-ons — small/medium/large) that the
flat 1 cm wall the app uses today overestimates interior volume by **4.5–5.4%** once a wheel well
and handle spine exist, because it treats the bag as outer-shell-minus-one-flat-wall. Neither
protrusion is modelled. Here's how to fix that on your actual bag, the night before the demo, with
a tape measure and nothing else.

### What to measure

Open your real carry-on (empty) and measure five numbers, in metres:

1. **Shell thickness.** At the zipper opening, where you can see the wall's cross-section,
   measure how thick the shell/wall material is (hardshell plastic, or a soft-sided bag's fabric
   + frame). This is `shell`.
2. **Wheel well height.** Look at the interior floor at the back of the bag, over the wheels.
   The floor bulges up there. Measure straight up from the true (lowest) interior floor to where
   that bulge stops — the height of the hump. This is `wellHeight`.
3. **Handle spine depth.** The telescoping handle runs inside a channel against the back interior
   wall, full height. Measure from the back interior wall straight forward (into the bag) to
   where that channel's bulge stops. This is `spineDepth`.
4. **Outer shell dimensions.** With the bag closed, tape-measure the outer shell at its widest
   point on each axis: width (side to side), height (floor to lid top), depth (front to back).
   This is what the app's suitcase scan already produces (`outerW/outerH/outerD`) — you don't
   need to re-measure it by hand unless you want a sanity check against the scan.

You do not need `wellDepth`/`wellWidth`/`spineWidth` (how far across the floor/wall the bulges
run) — the calibration below only needs how far each bulge intrudes, not its footprint.

### Where the numbers go

`Spike/ScanView.swift` calls `interiorBox(steadied, wall: suitcaseWallMeters, wallHeight:
suitcaseFloorWallMeters, wallDepth: suitcaseHandleWallMeters)`. All three constants live at the
top of `Spike/ScanView.swift` and default to `suitcaseWallMeters` (`0.01`), so nothing needs
wiring — just replace the values:

```
let suitcaseWallMeters: Float = shell                        // width: no protrusion on either side wall
let suitcaseFloorWallMeters: Float = shell + wellHeight       // floor: raised to clear the wells
let suitcaseHandleWallMeters: Float = shell + spineDepth      // back wall: pulled clear of the spine
```

`interiorBox` (`Spike/PlanAnchor.swift`) pays `wallHeight` once from the floor and `wallDepth`
once from the back (the handle side — see the frame note below); the front/opening face only
ever pays the plain `wall`, so there's no separate front-wall constant to set.

**Do not try to force one constant to cover all three axes.** `tests/swift/bag/main.swift`
section 4 measured what that costs: making a single flat wall big enough to be safe on height
*and* depth needlessly shrinks the width too (which has no wheel well or handle spine at all),
and gives up **61–66%** of the bag's volume across the three simulated bags — worse than doing
nothing. Three separate constants is what's already wired up; use them.

**Which physical wall is "back"?** `interiorBox`'s depth cut comes off the `+perp` face, where
`perp = (-axis.z, 0, axis.x)` and bag-frame z runs from 0 (the corner `PlanAnchor.origin` anchors
placements to) to `+depth` at that face — the same convention `tests/swift/bag/main.swift`
documents. That's an internal convention every reader (`PlanAnchor`, `ScanView`, `Geometry`'s
`heightMap`) already agrees on; a single tap scan has no signal for which real side of your bag
that lands on, so before trusting `suitcaseHandleWallMeters`, check the AR overlay: its back face
is the one only pulled in by `wall` on the opening side and by the larger `wallDepth` on the
other — confirm that thicker cut visually lines up with your bag's handle side. If it's on the
wrong wall, the fix is a `perp` sign flip, not a recalibration — flag it rather than guessing.

### How to check you got it right

Run `bash tests/swift/bag/run.sh` — it doesn't know your bag's numbers, but it proves the
*shape* of the calibration (three separate constants feeding the asymmetric depth cut) stays
inside the true interior across a range of bag sizes without giving away more than a stated
fraction. To check your own bag once the app is calibrated: scan the empty, closed suitcase, then
physically set one real item that just barely fits (by hand) into the back corner near the wheel
well and against the back wall. If the app's AR overlay would place an item there but it doesn't
actually fit, your `wellHeight`/`spineDepth` measurements were too small — remeasure. If the
overlay leaves a suspiciously large empty-looking gap along the back wall or floor everywhere,
not just near the wheel/handle, you're over-conservative — see the margin discussion below.

### The 24% margin (asymmetric depth is implemented)

Calibrating the three constants above is **safe** — it never reaches into the well or spine —
and it now pays the spine only once. `Spike/PlanAnchor.swift`'s `interiorBox` raises the floor by
`wallHeight` *and* shifts the box's center up by `wallHeight/2` (so the modeled ceiling still
touches the real ceiling exactly, zero waste there), and gives depth the matching treatment: pay
`wallDepth` once, from the back face, and shift `center` toward the opening by half the extra
intrusion (`wallDepth - wall`), so the modeled front face still touches the shell-only interior
exactly. `tests/swift/bag/run.sh` measures the result against the true interior:

```
small carry-on   modeled=21.24L true=28.11L margin=-24.4%
medium carry-on  modeled=31.54L true=41.42L margin=-23.9%
large carry-on   modeled=41.88L true=55.7L  margin=-24.8%
```

versus the old symmetric behavior (same wallDepth off both faces, `tests/swift/bag/main.swift`
section 2's hand-rolled baseline) at -39.6% / -39.1% / -40.7% — roughly **halving** the wasted
volume (+4.25L / +6.3L / +8.8L recovered) on the three simulated bags. It is still not a perfect
fit — BoxFit is a single axis-aligned box, and the wheel wells only eat into the back *corners*
(not the full width/depth), so even a "tight" per-axis box gives up space that's actually free
elsewhere. Carving that out exactly would need a compound (L-shaped) shape, which is a modelling
project, not a calibration — not worth it for a demo.

**Recommendation:** set the three constants above (`suitcaseWallMeters`, `suitcaseFloorWallMeters`,
`suitcaseHandleWallMeters`) from your real bag's measurements — the asymmetric depth cut is
already in place, so there's no follow-up code change to schedule. Don't attempt anything
fancier than that tonight.

### Is 1 cm the right default?

`suitcaseWallMeters` (`Spike/ScanView.swift:24`) is `0.01` today. The simulated bags above use
`shell = 0.010` for a hardshell carry-on, so 1 cm is a reasonable estimate for shell thickness
alone. But that's not what the constant currently does: wired as the single wall on every axis
(section 4 above), it is simultaneously **too small** to be safe (it's what produces the 4.5–5.4%
overestimate in section 1 — it doesn't clear the well or spine at all) and, if raised enough to
be safe on every axis, **far too wasteful** (61–66% given up, per bag above). There is no single
value of `suitcaseWallMeters` alone that is both safe and reasonably efficient once wheel wells
and a handle spine exist — this isn't a "pick a better constant" problem, it's the "one flat
constant" design that needs to become three. If tonight's fix (three constants, see above) can't
land in time, treat `suitcaseWallMeters` as a known-unsafe placeholder for any bag with wheels —
i.e. assume the AR overlay is optimistic by up to ~5% of interior volume for a typical hardshell
carry-on, and mention that to whoever is demoing.
