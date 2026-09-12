# Pipeline loop, background labelling, AR scan tests — design

2026-09-12, branch `loop`. Written for five parallel agents plus a self-paced monitoring loop.

## Goal

The whole pipeline — LiDAR scan → `POST /items` → Grok label → MongoDB → `POST /plan`
(packer3d + physics) → iOS plan/AR — runs end to end with one command, keeps running
while teammates land changes, and the parts that cannot be run on Linux (the AR scanner's
geometry, the plan→world transform) get tests and simulations that *do* run on Linux.

## Assumptions (Eric said "execute"; these are the readings taken)

1. "A loop that continues running, checking on the pipeline" = two loops:
   - **Session loop**: this Claude session re-runs `scripts/pipeline_check.sh` every
     ~20–30 min after fetching `origin/main`, fixes regressions, reports.
   - **Server loop**: a background task in `server/main.py` that keeps labelling items
     whose Grok call failed at upload, so every scan ends up catalogued.
2. "Make the AI API automatically label the items and catalog it into the database" =
   `POST /items` keeps labelling synchronously when Grok answers (the app's contract is
   unchanged), and the server loop above retries failures against the stored photo.
3. "AR component problems, tests and simulation" = `Spike/Geometry.swift` (hull, min-area
   rect, cluster, heightmap) gets a synthetic-scene simulation on Linux and its bugs fixed;
   the missing AR plan overlay gets a pure, tested bag→world transform and a minimal
   RealityKit overlay that only uses API calls already present in `ScanView.swift`.

## Contracts shared between agents

### Item document (server ↔ app)

Adds to `SCAN_OUTPUT.md`'s fields:

| Field | Meaning |
|---|---|
| `labelStatus` | `"done"` or `"pending"`. `pending` = Grok failed at upload; the server retries in the background. Present on every item. |
| `photo` | The uploaded JPEG bytes, stored in Mongo, **never returned** by any route (`public()` strips it). |

Routes added: `GET /items/{id}` (the public doc, 404 if missing).

Server loop: on startup, an asyncio task every `LABEL_RETRY_S` (default 10 s) finds
`{"labelStatus": "pending"}`, calls `detect(photo)` in a thread, and `$set`s only the
fields whose `*Source` is still `"auto"`; success → `labelStatus: "done"`. After
`LABEL_MAX_ATTEMPTS` (5) failures → `labelStatus: "failed"`. No key → items are `done`
with the `UNKNOWN` guess as today (nothing to retry).

App: after `API.upload`, if `labelStatus == "pending"`, poll `GET /items/{id}` every 3 s
for up to 60 s and replace the shown item when it changes.

### Bag frame → AR world

Per `packing-core/CLAUDE.md` option (a): plan origin is the bag interior's min corner;
bag X = the scanned suitcase `BoxFit.axis`, bag Y = world up, bag Z = `perp =
(-axis.z, 0, axis.x)`; origin = `center - axis·width/2 - perp·depth/2` at `y = planeY`.
A placement's world centre = `origin + axis·(p.x + s.x/2) + up·(p.y + s.y/2) + perp·(p.z + s.z/2)`.
This math lives in a pure file (no ARKit/RealityKit import) so it runs under `swiftc` on Linux.

### Swift on Linux

`tests/swift/SimdShim.swift` supplies `simd_dot/length/normalize/cross` and `simd_float2x2`
when `simd` is unavailable; app files import simd under `#if canImport(simd)`. Build with
`swiftc -Onone` (top-level code under `-O` trips a spurious exclusivity check). Each test dir
has its own `run.sh`; `make test-swift-app` runs them all.

## File ownership (one agent per file; no overlaps)

| Agent | Files |
|---|---|
| A1 server labeller | `server/main.py`, `server/check.py`, `SCAN_OUTPUT.md` (field table only) |
| A2 scan simulation | `Spike/Geometry.swift`, `tests/swift/scan/**`, `tests/main.swift` |
| A3 AR overlay + polling | `Spike/PlanAnchor.swift` (new), `Spike/ScanView.swift`, `Spike/SpikeApp.swift`, `Spike/API.swift`, `tests/swift/plan/**` |
| A4 adapter frames | `physics/packer3d_adapter.py`, `tests/test_packer3d_adapter.py`, `scripts/demo_e2e.py`, `docs/INTEGRATION.md` |
| A5 pipeline check | `scripts/pipeline_check.sh` (new), `Makefile`, `.github/workflows/ci.yml`, `scripts/README.md`, `packer3d/tests/test_thorough_edge_cases.py`, `packer3d/pyproject.toml` |

Each agent works in its own git worktree on its own branch off `loop`, commits there, and
reports the branch name. The session merges into `loop`, runs `scripts/pipeline_check.sh`,
and only then starts the monitoring loop. Nothing is pushed without Eric.

## Not doing

- Async upload (`label: null` immediately): the app would need a redesign; the sync path
  plus background retry covers the failure case, which is the one that loses scans.
- Storing runner-up solver placements, zones/wheel wells, folding, voxel nesting.
- README server section and `.env.example`: owned by Helen's branch.
