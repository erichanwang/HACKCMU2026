# Suitcase — AR Packing Assistant

Scan objects with iPhone LiDAR, store them in MongoDB, pack them optimally, guide the pack in AR. See `MVP.md` for the demo plan and team split, `SCAN_OUTPUT.md` for the scanned-object format.

```
Spike/     iOS app — LiDAR scan → ScannedItem → POST to server
server/    FastAPI + MongoDB — stores items, labels them with Claude
Tests/     self-check for the geometry code
physics/   deterministic physics validation (Python; Swift port in swift/PackPhysics)
pan/       IFM PAN world-model layer (counterfactual packing rollouts)
packer3d/  3D packing solver with centre-of-mass optimisation
```

## Server

Needs [uv](https://docs.astral.sh/uv/) (`brew install uv`). Everything else installs itself.

Copy `.env.example` to `.env` at the repo root and fill in `SUITCASE_MONGODB_URI` (Atlas connection string) and `ANTHROPIC_API_KEY` — ask Helen for both. `.env` is git-ignored.

```bash
cd server
uv run --env-file ../.env uvicorn main:app --host 0.0.0.0 --reload
```

`--reload` restarts the server whenever `main.py` changes, so you never run stale code.

Runs on port 8000. Endpoints:

| | |
|---|---|
| `POST /suitcases` | JSON `{"name": …, "dimensions": [w, h, d]}` in metres. Returns the suitcase with its `id`. |
| `GET /suitcases` | All suitcases, newest first. |
| `GET /suitcases/{id}` | One suitcase **with its `items`** — the solver's input. |
| `POST /label` | multipart: `image` (JPEG). Identifies the object via Claude and returns the guess; stores nothing. |
| `POST /items` | multipart: `item` (ScannedItem JSON) + optional `image` (JPEG). With an image, labels via Claude (vision); without, the item's own `label`/`rigidity` are kept as user-set. Stores and returns the item. |
| `PATCH /items/{id}` | JSON `{"label": …, "rigidity": "rigid"\|"soft"\|"fragile"}` — user override. |
| `GET /items?suitcaseId=…` | Scanned items, optionally filtered by suitcase. |
| `DELETE /items/{id}` | Remove one item. |
| `DELETE /suitcases/{id}` | Remove a suitcase and everything scanned into it. |

Optional env: `MONGO_DB` (default `suitcase`), `ANTHROPIC_MODEL` (default `claude-opus-5`). Without `SUITCASE_MONGODB_URI` it uses a local `mongodb://localhost:27017`.

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

## Using the app

1. **Suitcase screen** — scan a suitcase (close it, put it on the floor, tap the middle of the lid, **Use this suitcase**), type its interior dimensions, or pick an existing one. Swipe left to delete one.
2. **Scanner** — tap an object. The panel shows the dimensions, then *Identifying…*, then the guessed label and rigidity — **nothing is stored yet**. Edit if needed, then **Add to suitcase** (or **Discard**). **✓ Added** confirms it's in the database; **Next item** clears the panel. The camera is live the whole time.
3. **N items** (header) — everything in this suitcase. Tap one to edit, **Rescan to re-measure**, or delete. **Add → Add by hand** types in an item LiDAR can't measure (thin, shiny, transparent). Rescan keeps the item and anything you typed; only the measurement changes.

The Xcode console prints an ASCII heightmap and the JSON for every scan.

### Getting a good scan

LiDAR is about ±1 cm at best. These make the difference between that and a wild number:

- **One object on a clear surface**, at least 5 cm from anything else — neighbours get merged into the box.
- **Get close: 30–50 cm.** Accuracy drops fast with distance. Fill a good part of the screen with the object.
- **Hold still for a second before tapping** so the depth settles. The app refuses the tap if too few depth points land on the object (*Not enough detail*) — usually too far away or too small.
- **Tap the top face, near the centre.** Tapping an edge can seed the measurement on the table.
- **Matte, light-coloured objects scan well.** Black, glossy, or transparent things are what LiDAR can't see — measure those with a tape and type them.
- **Nothing smaller than ~5 cm, and nothing thinner than ~2 cm.** A flat iPad, book, or folded shirt can't be separated from the table it lies on — the app says so and you add it by hand (**N items → Add → Add by hand**).
- Measurements come straight from the LiDAR depth map (not the smoothed mesh you see on screen), using only high-confidence pixels, with the outermost 1% of points trimmed. The coloured mesh overlay is just a visual cue.
- **Keep some of the floor/table around the object in view.** The height of the surface it sits on is found from the depth points around it (the biggest band of points clearly below the tap), so the app needs to see that surface. If it can't, it says so — step back.
- If a number looks off, **Rescan** from the item's page — it's cheap.

Suitcase scans: same rules, plus stand back enough that the whole bag is on screen with margin. Usable interior = exterior × 0.92 per axis (about 78% of the volume — shell, wheels and handle housing); `suitcaseInteriorScale` in `Spike/ScanView.swift`.

Tuning knobs at the top of `Spike/ScanView.swift`: `paddingMeters`, `minHeightMeters`, `searchRadiusMeters`, `clusterCellMeters`, `shapeCellMeters`, `minClusterPoints`, `minDepthConfidence`, `trimFraction`, `suitcaseInteriorScale`.

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
