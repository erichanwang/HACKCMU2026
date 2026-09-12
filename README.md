# AR Packing Assistant

An iPhone app for HackCMU 2026 (Travel track). Point it at an open suitcase and the things going into it, and it computes a packing arrangement and walks you through placing each item in AR.

## Core loop

```
scan reality -> reconstruct digital twin -> compute packing -> simulate in 3D -> execute in AR
```

1. Scan a container and 4-6 physical objects with an iPhone/iPad.
2. The app reconstructs each object and the container as basic 3D geometry.
3. A packing solver returns collision-free positions for each object.
4. A 3D scene animates the objects into place.
5. AR mode overlays a ghost of each object in its real-world spot, one at a time, until the bag is packed.

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
