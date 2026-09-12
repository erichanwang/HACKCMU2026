# PackAR

**Scan your stuff, get a packing plan, see exactly where everything goes — in AR.**

PackAR scans items with an iPhone's LiDAR, figures out the optimal way to pack them into a suitcase (or any container), and overlays the plan onto your real luggage so you just place things where the app shows.

## Run it

Needs Docker and [uv](https://docs.astral.sh/uv/). Two terminals from the repo root:

```bash
docker compose --env-file /dev/null up -d          # Mongo 7 on localhost:27017
cd server && uv run --env-file ../.env uvicorn main:app --host 0.0.0.0
```

`docker compose --env-file /dev/null down` stops Mongo again; the named volume keeps the data.
The `--env-file /dev/null` is only there because compose parses the repo's `.env` for variable
interpolation even though `docker-compose.yml` uses none, and one stray line in `.env` is then
enough to make `docker compose` refuse to run.

Both flags on the second command are load-bearing:

- **`--env-file ../.env`** — nothing else loads `.env`. A plain `uvicorn main:app` starts fine
  with no `XAI_API_KEY`, and then every scanned item comes back labelled `"unknown"`
  (`labelStatus: "pending"`) instead of being classified by Grok.
- **`--host 0.0.0.0`** — the phone reaches the server over Wi-Fi, not localhost. Point the app
  at the Mac running it: `ipconfig getifaddr en0`, put that IP in `API.base` in
  `Spike/API.swift`, and keep phone and Mac on the same network.

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
| `DELETE /suitcases/{id}` | Drops the suitcase, its items and its plan. |
| `POST /suitcases/{id}/plan` | Runs the solver + physics validation over that suitcase's items, stores the plan. |
| `GET /suitcases/{id}/plan` | The stored plan (404 until you POST it). |
| `POST /items` | multipart: `item` (ScannedItem JSON, **must include `suitcaseId`**) + `image` (JPEG, ≤10MB). Labels via Grok, stores, returns the item. |
| `GET /items` | All items, or one suitcase's with `?suitcaseId=…`. |
| `GET /items/{id}` | One item; the app polls this while `labelStatus` is `"pending"`. |
| `PATCH /items/{id}` | JSON with any of `label`, `rigidity` (`rigid`/`soft`/`fragile`), `compressibility`, `mass`, `keepUpright` — user override. |
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
server/         FastAPI + MongoDB — stores items, labels them with Grok
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
