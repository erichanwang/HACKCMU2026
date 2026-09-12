# PackAR

**Scan your stuff, get a packing plan, see exactly where everything goes — in AR.**

PackAR scans items with an iPhone's LiDAR, figures out the optimal way to pack them into a suitcase (or any container), and overlays the plan onto your real luggage so you just place things where the app shows.

## The problem

Packing a suitcase well is a 3D bin-packing problem most people solve badly by eye. Existing packing-cube advice is generic; nothing looks at *your* actual items and *your* actual suitcase and tells you where each thing goes. PackAR does.

## How it works

1. **Scan** — point your iPhone at an item, PackAR's LiDAR-based scanner captures its bounding box and geometry.
2. **Label** — the server identifies and classifies the item (rigid / soft / fragile) via Claude, stored in MongoDB.
3. **Pack** — a 3D packing solver arranges all scanned items into the container, optimizing for space and stability (center of mass, support, no overlaps).
4. **Validate** — a physics layer checks the plan is actually physically realizable before showing it to anyone.
5. **Guide** — the plan is rendered as a layered AR/2D diagram so you can see exactly where each item belongs.

## What's demoable right now

- iOS LiDAR scanning → structured item data → server (`Spike/`, `server/`)
- 3D packing solver with center-of-mass optimization (`packer3d/`)
- Physics validation of any proposed layout — collision, containment, support, fragility (`physics/`, Swift port in `swift/PackPhysics`)
- Counterfactual "what if I packed it differently" rollouts via the PAN world-model layer (`pan/`)
- Packing plan viewer — 2D layered diagram UI, Swift package (`packing-core/`)

## Why this is a hard problem, not a toy one

- **LiDAR is noisy** — centimeter-scale, not the precision naive packing math assumes.
- **Most of what you pack isn't rigid** — clothes compress, fold, and fill gaps that rigid bin-packing can't model.
- **Suitcases aren't boxes** — wheel wells, handle rails, and lid pockets eat 10-15% of "usable" rectangular volume.

See `OVERVIEW.md` for how each of these is actually handled.

## Repo layout

```
Spike/          iOS app — LiDAR scan → ScannedItem → POST to server
server/         FastAPI + MongoDB — stores items, labels them with Claude
packer3d/       3D packing solver with centre-of-mass optimisation
physics/        deterministic physics validation (Python; Swift port in swift/PackPhysics)
pan/            IFM PAN world-model layer (counterfactual packing rollouts)
packing-core/   Swift package — packing plan model + 2D layer diagram UI
tests/          physics self-checks, property tests, synthetic fixtures
```

## Docs

| File | What's in it |
|---|---|
| [`OVERVIEW.md`](OVERVIEW.md) | Full product/technical writeup: the hard-problem breakdown above in depth, the pipeline, the stack, and where this could go beyond travel (foam case layout, fulfillment cartons, field kits). |
| [`PRD.md`](PRD.md) | Product requirements: goals, non-goals, target users, hackathon scope vs. roadmap. |
| [`MVP.md`](MVP.md) | The hackathon demo spec: one container, 4-6 rigid items, exact data shapes between scan and solver. |
| [`docs/PHYSICS.md`](docs/PHYSICS.md) | Technical reference for the physics validation layer. |
| [`SCAN_OUTPUT.md`](SCAN_OUTPUT.md) | The scanned-item JSON format. |

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
