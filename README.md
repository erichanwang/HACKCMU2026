# PackAR

**Scan your stuff, get a packing plan, see exactly where everything goes — in AR.**

PackAR scans items with an iPhone's LiDAR, figures out the optimal way to pack them into a suitcase (or any container), and overlays the plan onto your real luggage so you just place things where the app shows.

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
