# PackAR

**Scan your stuff, get a packing plan, see exactly where everything goes — in AR.**

PackAR scans items with an iPhone's LiDAR, figures out the optimal way to pack them into a suitcase (or any container), and overlays the plan onto your real luggage so you just place things where the app shows.

**[Watch the demo](https://youtube.com/shorts/ixLURxjuSVw)**

## Run it

Needs Docker and [uv](https://docs.astral.sh/uv/). Two terminals from the repo root:

```bash
docker compose up -d                               # Mongo 7 on localhost:27017
cd server && uv run --env-file ../.env uvicorn main:app --host 0.0.0.0
```

`docker compose down` stops Mongo again; the named volume keeps the data.
Compose parses the repo's `.env` for variable interpolation even though `docker-compose.yml` uses
none, so one stray non-`KEY=value` line in `.env` is enough to make it refuse to run. If that
happens, `docker compose --env-file /dev/null up -d` ignores the file entirely.

Both flags on the second command are load-bearing:

- **`--env-file ../.env`** — nothing else loads `.env`. A plain `uvicorn main:app` starts fine
  with no `XAI_API_KEY`, and then every scanned item comes back labelled `"unknown"`
  (`labelStatus: "pending"`) instead of being classified by Grok.
- **`--host 0.0.0.0`** — the phone reaches the server over Wi-Fi, not localhost. Get the Mac's
  address with `ipconfig getifaddr en0`, then open the app's Settings sheet from the gear at the
  top right and type it as `http://<ip>:8000`. It is kept in `UserDefaults`, so this
  survives a relaunch and needs no rebuild. Keep phone and Mac on the same network. (A
  `PACKAR_SERVER` scheme variable and a hard-coded fallback back it up, in that order — but
  neither can be changed while standing in a demo line, which is what the settings sheet is
  for.)

Mongo must be reachable or the server does not boot. Override with `SUITCASE_MONGODB_URI`
(default `mongodb://localhost:27017`) to point at Atlas instead; `MONGO_DB` (default
`suitcase`) and `GROK_MODEL` (default `grok-4`) are the other knobs. Smoke test:
`cd server && uv run python check.py` → prints `server ok`.

Endpoints (port 8000):

| | |
|---|---|
| `POST /suitcases` | JSON `{"name": …, "dimensions": [w, h, d]}` in metres. The container everything packs into. |
| `GET /suitcases` | All suitcases, newest first. |
| `GET /suitcases/{id}` | One suitcase plus its items. |
| `DELETE /suitcases/{id}` | Drops the suitcase and its plan; its items are detached (`suitcaseId: null`) and stay in the owner's inventory. |
| `POST /suitcases/{id}/plan` | Runs the solver + physics validation over that suitcase's items, stores the plan. |
| `GET /suitcases/{id}/plan` | The stored plan (404 until you POST it). |
| `POST /items` | multipart: `item` (ScannedItem JSON, **must include `suitcaseId`**) + `image` (JPEG, ≤10MB). Labels via Grok, stores, returns the item. |
| `GET /items` | All items, or one suitcase's with `?suitcaseId=…`. |
| `GET /items/{id}` | One item; the app polls this while `labelStatus` is `"pending"`. |
| `GET /inventory` | Everything the signed-in user has scanned, across suitcases, newest first. Survives a suitcase delete. |
| `PATCH /items/{id}` | JSON with any of `label`, `rigidity` (`rigid`/`soft`/`fragile`), `compressibility`, `mass`, `keepUpright` — user override — or `suitcaseId` (one of your suitcases, or `null` to take the item out of any); a move drops both bags' stored plans. |
| `DELETE /items/{id}` | Drops the item and invalidates the suitcase's stored plan. |

Mutating routes take an Auth0 bearer token; with `AUTH0_DOMAIN`/`AUTH0_AUDIENCE` unset (the
local default) they run open, as a single shared `local` user.

## The problem

Packing a suitcase well is a 3D bin-packing problem most people solve badly by eye. Existing packing-cube advice is generic; nothing looks at *your* actual items and *your* actual suitcase and tells you where each thing goes. PackAR does.

## How it works

1. **Scan** — point your iPhone at an item, PackAR's LiDAR-based scanner captures its bounding box and geometry.
2. **Label** — the server identifies and classifies the item (rigid / soft / fragile) via Grok, stored in MongoDB.
3. **Pack** — a 3D packing solver arranges all scanned items into the container, optimizing for space and stability (center of mass, support, no overlaps).
4. **Validate** — a physics layer checks the plan is actually physically realizable before showing it to anyone.
5. **Guide** — the plan is rendered as a layered AR/2D diagram so you can see exactly where each item belongs.

## What's demoable right now

- iOS LiDAR scanning → structured item data → server (`Spike/`, `server/`)
- 3D packing solver with center-of-mass optimization (`packer3d/`)
- Physics validation of any proposed layout — collision, containment, support, fragility (`physics/`, Swift port in `swift/PackPhysics`)
- Counterfactual "what if I packed it differently" rollouts via the PAN world-model layer (`pan/`) — mock world-model only; the real PAN backend is pending API access (`docs/PAN_ACCESS.md`)
- Packing plan viewer — 2D layered diagram + 3D orbit viewer under one picker, Swift package (`packing-core/`)

## Why this is a hard problem, not a toy one

- **LiDAR is noisy** — centimeter-scale, not the precision naive packing math assumes.
- **Most of what you pack isn't rigid** — clothes compress, fold, and fill gaps that rigid bin-packing can't model.
- **Suitcases aren't boxes** — wheel wells, handle rails, and lid pockets eat 10-15% of "usable" rectangular volume.

See `OVERVIEW.md` for how each of these is actually handled.

## Repo layout

```
Spike/          iOS app — LiDAR scan → ScannedItem → POST to server
server/         FastAPI + MongoDB — stores items, labels them with Grok
packer3d/       3D packing solver with centre-of-mass optimisation
physics/        deterministic physics validation (Python; Swift port in swift/PackPhysics)
pan/            IFM PAN world-model layer (counterfactual packing rollouts, mock backend)
packing-core/   Swift package — packing plan model, 2D layer diagram, 3D orbit viewer
swift/          PackPhysics — the physics layer ported to Swift, runs on Linux
tools/          plan3d (renders a plan to SVG without a Mac), packbench (solver benchmark)
scripts/        end-to-end demo and pipeline checks
examples/       sample scans, scenes and placements
docs/           technical references (table below)
tests/          physics self-checks, property tests, synthetic fixtures
```

## Docs

| File | What's in it |
|---|---|
| [`OVERVIEW.md`](OVERVIEW.md) | Full product/technical writeup: the hard-problem breakdown above in depth, the pipeline, the stack, and where this could go beyond travel (foam case layout, fulfillment cartons, field kits). |
| [`PRD.md`](PRD.md) | Product requirements: goals, non-goals, target users, hackathon scope vs. roadmap. |
| [`MVP.md`](MVP.md) | The hackathon demo spec: one container, 4-6 rigid items, exact data shapes between scan and solver. |
| [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) | The three-minute demo, step by step, written from the code as it stands. |
| [`docs/AR_BUILD.md`](docs/AR_BUILD.md) | Building and running the iOS app on a Mac, and what Linux can and cannot prove first. |
| [`docs/PHYSICS.md`](docs/PHYSICS.md) | Technical reference for the physics validation layer. |
| [`docs/PLAN_3D.md`](docs/PLAN_3D.md) | The 3D plan viewer, and `tools/plan3d` for checking its geometry without a Mac. |
| [`docs/INTEGRATION.md`](docs/INTEGRATION.md) | Wiring the physics layer into the scan → solver → renderer loop. |
| [`docs/SOLVER_INTEGRATION.md`](docs/SOLVER_INTEGRATION.md) | How `physics/packer3d_adapter.py` maps solver JSON into scenes and back. |
| [`docs/PAN_ACCESS.md`](docs/PAN_ACCESS.md) | What PAN access we actually have: mock backend now, no public API as of 12 Sep. |
| [`SCAN_OUTPUT.md`](SCAN_OUTPUT.md) | The scanned-item JSON format. |
