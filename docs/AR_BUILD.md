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

`Spike/ScanView.swift:123` currently calls `interiorBox(box, wall: suitcaseWallMeters)` with one
constant (`suitcaseWallMeters`, `Spike/ScanView.swift:24`, currently `0.01`). `interiorBox`
(`Spike/PlanAnchor.swift:15`) already accepts two more, `wallHeight`/`wallDepth`, that default to
`wall` when omitted — they just aren't wired up yet. To calibrate:

```
let suitcaseWallMeters: Float = shell            // width: no protrusion on either side wall
let suitcaseWallHeightMeters: Float = shell + wellHeight   // floor: raised to clear the wells
let suitcaseWallDepthMeters: Float = shell + spineDepth    // back wall: pulled clear of the spine
```

and change the call to
`interiorBox(box, wall: suitcaseWallMeters, wallHeight: suitcaseWallHeightMeters, wallDepth: suitcaseWallDepthMeters)`.

**Do not try to force one constant to cover all three axes.** `tests/swift/bag/main.swift`
section 4 measured what that costs: making a single flat wall big enough to be safe on height
*and* depth needlessly shrinks the width too (which has no wheel well or handle spine at all),
and gives up **61–66%** of the bag's volume across the three simulated bags — worse than doing
nothing. Three separate constants is a ~3-line change, not a redesign; do that instead.

### How to check you got it right

Run `bash tests/swift/bag/run.sh` — it doesn't know your bag's numbers, but it proves the
*shape* of the calibration (three separate constants, not one) stays inside the true interior
across a range of bag sizes without giving away more than a stated fraction. To check your own
bag once the app is calibrated: scan the empty, closed suitcase, then physically set one real
item that just barely fits (by hand) into the back corner near the wheel well and against the
back wall. If the app's AR overlay would place an item there but it doesn't actually fit, your
`wellHeight`/`spineDepth` measurements were too small — remeasure. If the overlay leaves a
suspiciously large empty-looking gap along the back wall or floor everywhere, not just near the
wheel/handle, you're over-conservative — see the margin discussion below.

### The 40% problem, and what to do about it tonight

Calibrating three separate constants as above (measured in `tests/swift/bag/main.swift` section
2) is **safe** — it never reaches into the well or spine — but it costs real capacity:

```
small carry-on   modeled=16.99L true=28.11L margin=-39.6%
medium carry-on  modeled=25.23L true=41.42L margin=-39.1%
large carry-on   modeled=33.06L true=55.7L  margin=-40.7%
```

That ~40% comes from a specific bug in the symmetry, not from the wells/spine themselves: `wall`
and `wallDepth` in `interiorBox` shrink the box by the *same* amount on both the back face (where
the spine actually is) and the front/opening face (where nothing intrudes), because the box's
z-center never moves. Height doesn't have this problem — `interiorBox` already raises the floor
by `wallHeight` *and* shifts the box's center up by `wallHeight/2`, so the modeled ceiling still
touches the real ceiling exactly, zero waste. Depth needs the same treatment and doesn't get it.

`tests/swift/bag/main.swift` section 3 models the fix by hand — pay `spineDepth` once, shift the
box's center back by `spineDepth/2` toward the opening — since it needs a code change to
`interiorBox`/`PlanAnchor.swift` (out of scope for this doc; the recipe below is for whoever picks
it up). That drops the margin to:

```
small carry-on   modeled=21.24L true=28.11L margin=-24.4%
medium carry-on  modeled=31.54L true=41.42L margin=-23.9%
large carry-on   modeled=41.88L true=55.7L  margin=-24.8%
```

roughly **halving** the wasted volume (+4.25L / +6.3L / +8.8L recovered) for a few lines of code.
It is still not a perfect fit — BoxFit is a single axis-aligned box, and the wheel wells only eat
into the back *corners* (not the full width/depth), so even a "tight" per-axis box gives up space
that's actually free elsewhere. Carving that out exactly would need a compound (L-shaped) shape,
which is a modelling project, not a calibration — not worth it for a demo.

**Recommendation for tonight:** use the three-constant symmetric calibration above (the ~40%
number) — it needs zero new logic, only two new constants and the already-tested formula from
`suitcaseWallMeters + wellHeight`/`spineDepth`. If someone has ~15 spare minutes and is
comfortable touching `Spike/PlanAnchor.swift`, implement the asymmetric depth fix from section 3
(pay `spineDepth` once, shift center.z by `-spineDepth/2`) — it's a real, measured 15-point
improvement for a small, well-understood change. Don't attempt anything fancier than that
tonight.

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
