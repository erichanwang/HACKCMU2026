# Suitcase — AR Packing Assistant

Scan objects with iPhone LiDAR, store them in MongoDB, pack them optimally, guide the pack in AR. See `MVP.md` for the demo plan and team split, `SCAN_OUTPUT.md` for the scanned-object format.

```
Spike/     iOS app — LiDAR scan → ScannedItem → POST to server
server/    FastAPI + MongoDB — stores items, labels them with Grok
Tests/     self-check for the geometry code
physics/   deterministic physics validation (Python; Swift port in swift/PackPhysics)
pan/       IFM PAN world-model layer (counterfactual packing rollouts)
packer3d/  3D packing solver with centre-of-mass optimisation
```

## Server

Needs [uv](https://docs.astral.sh/uv/) (`brew install uv`). Everything else installs itself.

```bash
cd server
export SUITCASE_MONGODB_URI='mongodb+srv://…'   # Atlas connection string — ask Helen
export XAI_API_KEY='…'                           # Grok key — ask Helen; omit and items are labelled "unknown"
uv run uvicorn main:app --host 0.0.0.0
```

Runs on port 8000. Endpoints:

| | |
|---|---|
| `POST /items` | multipart: `item` (ScannedItem JSON) + `image` (JPEG). Labels via Grok, stores, returns the item. |
| `PATCH /items/{id}` | JSON `{"label": …, "rigidity": "rigid"\|"soft"\|"fragile"}` — user override. |
| `GET /items` | All scanned items. This is what the solver and 3D viewer read. |

Optional env: `MONGO_DB` (default `suitcase`), `GROK_MODEL` (default `grok-4`). Without `SUITCASE_MONGODB_URI` it uses a local `mongodb://localhost:27017`.

Smoke test (needs a reachable Mongo): `uv run python check.py` → prints `server ok`.

## iOS app

Needs Xcode 16+, an iPhone with LiDAR (any Pro model), and `xcodegen` (`brew install xcodegen`). The `.xcodeproj` is not committed — generate it:

```bash
xcodegen generate
open Spike.xcodeproj
```

Then in Xcode: select the **Spike** target → **Signing & Capabilities** → choose your Team. Plug in the phone, pick it as the destination, ⌘R.

Before running, point the app at the machine running the server: edit `API.base` in `Spike/API.swift` to that Mac's LAN IP (`ipconfig getifaddr en0`). Phone and Mac must be on the same Wi-Fi.

Gotchas:
- If Xcode says the bundle identifier is not available, change `bundleIdPrefix` in `project.yml` to something unique to you and re-run `xcodegen generate`.
- First run on a phone: enable **Settings → Privacy & Security → Developer Mode**, then trust your certificate under **Settings → General → VPN & Device Management**.

Using it: point at an object on a table, pan for a couple of seconds until the mesh overlay covers it, tap the object. The Xcode console prints an ASCII heightmap and the JSON; the screen shows dimensions and the label/rigidity guess, both editable.

## Geometry self-check

```bash
swiftc -O Spike/Geometry.swift Tests/main.swift -o /tmp/geocheck && /tmp/geocheck
```

## Conventions

- Metres everywhere. `dimensions = [width, height, depth]`, X right, Y up, Z forward.
- Work on a branch, open a PR to `main`. No co-author trailers on commits.
- Never commit credentials. Env vars only.

---

## What's here

The iOS app and packing solver aren't built yet. What exists on `main` today is the product spec and a physics validation layer written in Python:

- `physics/`: checks whether a proposed layout (a container plus a list of positioned objects) is physically valid: objects inside the container, not overlapping, adequately supported, and consistent with per-object constraints like "fragile" or "keep upright." `physics/validator.py` is the entry point (`validate_layout(scene)`); everything else is a module it composes (collision via SAT on oriented bounding boxes, containment, support, compressibility for soft items, constraint checks).
- `tests/`: unit tests per module, property-based stress tests, synthetic travel-scene fixtures, and a benchmark script.
- `docs/PHYSICS.md`: the technical reference for the physics layer, covering coordinate conventions, why OBBs instead of AABBs or full meshes, collision epsilon semantics, and the public API.

This is the layer any future solver or AR guidance code will call to check its own output before showing it to a user.

## Docs

| File | What's in it |
|---|---|
| [`OVERVIEW.md`](OVERVIEW.md) | Full product and technical writeup: why suitcase packing is a hard geometry problem, the capture/geometry/solver/guidance pipeline, the stack, the build order, and where this could make money beyond travel (foam case layout, fulfillment cartons, field kits). |
| [`PRD.md`](PRD.md) | Product requirements. Goals, non-goals, target users, success metrics, and what's in scope for the hackathon versus the longer-term roadmap. |
| [`MVP.md`](MVP.md) | The hackathon demo spec specifically: one container, 4-6 rigid items, the exact data shapes the scan and solver stages pass around. |
| [`docs/PHYSICS.md`](docs/PHYSICS.md) | Technical reference for the physics validation layer in `physics/`. |

Start with `MVP.md` if you're building the demo. Read `OVERVIEW.md` for the reasoning behind the design decisions.

## Running the physics tests

```
pip install numpy pytest
pytest tests/
```

## Why this is harder than bin packing

Three things break the textbook solution:

- **The depth sensor is coarse.** iPhone depth data is centimeter-scale noise, not the sub-millimeter precision packing actually needs.
- **Most contents aren't rigid.** Clothes are most of a suitcase's volume and compress, fold, and stuff into gaps that rigid bin-packing math can't see.
- **Suitcase interiors aren't boxes.** Wheel wells, handle rails, and a lid compartment eat 10-15% of the volume a rectangular-prism model would assume is usable.

`OVERVIEW.md` covers how each of these gets handled.

## Scope for the hackathon

One rectangular container, 4-6 rigid travel items, synthetic cuboids first and then measured bounding boxes. Soft-item compression, multi-compartment interiors, and airline-rule checking are explicitly out of scope for the demo. See `PRD.md` for the full cut line.
