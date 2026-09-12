# FIXES

What still needs fixing, as of 2026-09-12 on branch `integration`. Ordered by how much it
blocks the demo. Each item names the file, what is wrong, and the fix.

## 1. Blockers: the demo cannot run until these are done

- **The iOS side has never been compiled.** `Spike/SpikeApp.swift`, `Spike/ScanView.swift`,
  `Spike/API.swift`, `Spike/Geometry.swift` (Suitcase/Item modes, `suitcaseId`, Pack button,
  plan sheet) and `project.yml` (PackPhysics package) were written on Linux with `swiftc -parse`
  only. On a Mac: `xcodegen generate`, build, fix the type errors that appear, run the loop
  scan suitcase → scan item → Pack → diagram.
- **`Spike/API.swift:21`** hard-codes `http://172.26.48.172:8000`. Change it to the Mac running
  the server (`ipconfig getifaddr en0`), or read it from a setting.
- **Mongo must be reachable when the server starts** (`server/main.py` pings at import). Either
  set `SUITCASE_MONGODB_URI` to the Atlas cluster or run
  `docker run --rm -d -p 27017:27017 mongo:7` before `uvicorn`. `README.md` does not say this.
- **`.env` is not loaded by anything.** Run the server as
  `cd server && uv run --env-file ../.env uvicorn main:app --host 0.0.0.0`, otherwise Grok is
  off and every item is labelled "unknown".

## 2. Bugs

- **`server/main.py:60` a Grok failure is a 500 on the upload.** `detect()` calls
  `raise_for_status()`; a 429/5xx/timeout from x.ai fails `POST /items` and the phone shows
  "server error". Catch `httpx.HTTPError`, store the `UNKNOWN` guess, and let the user relabel.
- **`server/main.py:129` no validation of the item payload.** `dimensions` with the wrong
  length, negative or NaN values, or a ragged `heights` grid are stored as-is and only blow up
  later inside `POST /plan` (a `ValueError` from `Item.from_scanned_heightmap` becomes a 500).
  Validate the three dimensions > 0 and the grid shape at upload; reply 422.
- **`physics/packer3d_adapter.py:232` vs `:272` double-rotate.** `scene_from_packer3d` bakes
  the solver's axis permutation into each object's `dimensions` with identity rotation;
  `placements_from_packer3d` returns the same permutation as a rotation. Pairing them (the
  obvious thing to do) rotates permuted items twice and the physics gate rejects them.
  `scripts/demo_e2e.py` works around it by passing positions only. Make one of them the
  canonical form and document it, or add a `scene_from_packer3d(..., unoriented=True)`.
- **`packer3d/tests/test_thorough_edge_cases.py:421`** always fails on this machine (65–75 s
  against a 15 s wall-clock budget, also on untouched code). Either the 1000-item first-fit
  path regressed or the budget is machine-specific; profile it, then fix or mark it `slow`.

## 3. Risks that will bite in a real demo

- **Scanning the suitcase gives its outer box.** `Spike/ScanView.swift` Suitcase mode fits the
  tapped object's bounding box, so walls and lid thickness count as interior and the plan is
  optimistic by a few centimetres. Scan it closed, or subtract a wall thickness constant
  before `POST /suitcases`. There are no zones or wheel wells (`OVERVIEW.md` promises both).
- **Items pack as bounding boxes.** `packer3d/packer3d/models.py:211` uses the heightmap only
  to classify box/cylinder/irregular and to measure volume. An open shoe or a bag with a dip
  gets no nesting. The scan already carries the data; the solver would need a voxel or
  footprint collision test to use it.
- **Grok is slow and synchronous.** 5–6 s per item inside `POST /items`; five items is half a
  minute of "labelling…". Return the item immediately with `label: null` and label in a
  background task, or accept it for the demo.
- **`POST /plan` is CPU-bound for ~3 s** (`server/planner.py:32`, four solver runs). Under one
  uvicorn worker the phone's next upload waits. Fine for one user; run `--workers 2` for a
  booth, or cut `CANDIDATES` to two.
- **`server/main.py` re-`POST /plan` while one runs:** both compute, last write wins. Harmless
  today, but the app's Pack button is not disabled while packing.
- **Only the best solver result is stored.** `server/planner.py` keeps summaries of the other
  three candidates (`alternatives`) but not their placements, so PAN and the UI cannot show
  "here is the runner-up". Store the top two results if that comparison is wanted.
- **`pan/evaluate.py:54` colour-segmentation proxy.** Two palette colours closer than 60 RGB
  units (teal vs shaded blue is one documented pair) merge, so `execution_risk` from the mock
  can be wrong. Segment by hue, or give each object an id-coloured mask channel.

## 4. Missing versus the spec

- **AR guidance.** `PRD.md`/`MVP.md` end with "overlay the plan on the real suitcase in AR".
  The app ends at `PlanDiagramView` (2D, layer by layer). Nothing places the plan into the
  ARKit scene.
- **PAN in the app.** World-model rollouts exist only in `scripts/demo_e2e.py` and
  `python3 -m pan demo`, and are a mock or K2-Horizon text reasoning, labelled as such. If the
  pitch says "imagine before you pack", that claim has no visual backing; say "physics-checked
  plan with an LLM risk read" instead.
- **Editing Grok's guesses in the app.** `Spike/SpikeApp.swift:69-81` lets the user fix label
  and rigidity; compressibility, mass and keepUpright are shown but not editable, although
  `PATCH /items/{id}` accepts all of them.
- **No delete or reset.** No route removes a suitcase, an item or a plan; every demo run
  leaves rows behind (`scripts/demo_e2e.py --keep` is a no-op for that reason). Add
  `DELETE /suitcases/{id}` cascading to its items and plan.
- **Folding.** Compression is a height squash (`physics/prepack.py`, `Item.compressed`). There
  is no fold/reshape model; a shirt stack is one box that gets shorter.

## 5. Two PAN layers

- `pan/world_model.py:636` `RealPanBackend` is an HTTP seam for a service whose route was never
  confirmed (`PAN_ENDPOINT_PATH`), so it is never available; `physics/pan.py` `RealPanBackend`
  is the one that actually talks to IFM (K2-Horizon, text). Two `MockPanBackend`s, two
  `simulate_candidate_actions`, two result types, two docs (`PAN.md`, `docs/PAN_INTEGRATION.md`,
  `docs/PAN_ACCESS.md`). Delete the dead HTTP seam and point `pan.world_model.get_world_model`
  at the IFM client, or drop the `pan/` visual layer from the demo path and keep the text one.
- `pan/world_model.py:540` `MockPanBackend.persist` duplicates `pan/demo.py::persist_result` and
  has no production caller.
- IFM calls time out (45 s observed once) and are retried by the caller only by re-running the
  script. `physics/pan.py` should retry once or lower the timeout.

## 6. Stale docs, config and tests

- **`README.md:70-110`** still says "The iOS app and packing solver aren't built yet" and
  "Soft-item compression … explicitly out of scope". Both are false now. The server section
  omits Mongo, `--env-file`, the `/plan` routes and the compressibility fields.
- **`.env.example`** lists only `PAN_*`; the code reads `XAI_API_KEY`, `GROK_MODEL`,
  `SUITCASE_MONGODB_URI`, `MONGO_DB`, `IFM_API_KEY`, `PAN_ENDPOINT_PATH` as well
  (`server/main.py`, `physics/pan.py`, `pan/world_model.py`). Eric's `.env` keeps the IFM key
  under `PAN_API_KEY`; `physics/pan.py` wants `IFM_API_KEY` (the demo script bridges it).
- **`.github/workflows/ci.yml`** runs only the root `unittest` suite, the PAN mock demo and the
  Swift tests. Not run: `packer3d/tests` (pytest), `tests/test_pan.py` (pytest-style, counts as
  0 tests under `unittest discover`), `server/check.py` (needs Mongo; a `mongo:7` service
  container would do). `requirements.txt` lacks `pytest`.
- **`Dockerfile`** only runs the test suites; there is no image that runs the server.
- **`examples/scanned_item.json`** is in the old centimetre `width/depth/height` form; the phone
  now sends metres and `dimensions`. `examples/README.md` describes the old form.
- **`MVP.md`** "rigid objects only, no deformable-body simulation" predates compressibility.
- **`IMPORTANT.md`** (untracked in the main checkout) says "Deployment: Vercel + Supabase"; the
  stack is FastAPI + Mongo on a laptop.
- **`packer3d/README.md` and `packer3d/ALGORITHM.md`** do not mention `Item.compressed`,
  `compressibility_k`, or the server-document input form the loader now accepts.
- **`docs/PAN_INTEGRATION.md`** documents the visual-rollout contract as if a visual backend
  existed; add the honesty-note requirement it now carries to `README.md`'s PAN mention.

Concrete doc-vs-code mismatches (each verified by grep; fix the doc unless noted):

- `README.md:59` runs `swiftc … Tests/main.swift`; the file is `tests/main.swift` (fails on a
  case-sensitive filesystem). `README.md:8` likewise calls the directory `Tests/`.
- `README.md:27-31` endpoint table lacks every `/suitcases` route and the `/plan` routes, and
  does not say `POST /items` now requires `suitcaseId`. `SCAN_OUTPUT.md:9-44` sample and field
  table omit `suitcaseId` too.
- `docs/PHYSICS.md:749-755` and `docs/INTEGRATION.md:121-128` list the CLI as `validate`,
  `example`, `scan-to-object`; `physics/__main__.py` also has `validate-packer3d`
  (`--strategy`, `--items`, `--pretty`). `docs/PHYSICS.md:5` "ten modules" is now fifteen.
- `OVERVIEW.md` stack table (Rust/C++ solver behind Swift FFI, Metal voxel ops, SQLite/Core
  Data, CloudKit, Vision/mobile-SAM masks): none of it exists; the stack is Python packer3d +
  physics, FastAPI, MongoDB, Grok. Rewrite the table or label it "original plan".
- `docs/PAN_ACCESS.md:24,88-91`: "only PAN.md mentions PAN", "no .env* in the tree", and a
  four-variable config table are all stale (`pan/`, `physics/pan.py`, `.env.example`,
  `PAN_ENDPOINT_PATH`).
- `docs/SWIFT_PORT.md:32-40` says Validator/Incremental have no tests and counts 118; there
  are `ValidatorTests.swift`, `IncrementalTests.swift`, 178 tests. `docs/SWIFT_PORTABILITY.md:3`
  says 163 tests, and its `:34-38` "reported, not fixed" `bench --objects -5` crash is guarded at
  `Sources/PackPhysicsCLI/main.swift:198`.
- `packer3d/README.md:14,251-265` "42 edge-case tests" (103 now); module map omits
  `physics_bridge.py` and `geometry.py`; `OptimizerConfig`/`DecoderParams` field lists omit
  `multi_start` and `chunk`. `packer3d/ALGORITHM.md:380` "112 tests" (103).
- `scripts/README.md:22` omits `scenario.json`, which the script writes and reads back.

## 7. Branch and worktree hygiene

- `integration` (this branch) holds Gaps 1–5, the physics pre-pass and ranked search;
  `render-polish` holds the faster 3D solver render and the z-buffered PAN observation
  renderer. Neither is pushed. Land `integration` first, then `render-polish`
  (`pan/fixtures/*.png` will be stale after the renderer lands: `python3 pan/render_fixtures.py`).
- Local branches `compressibility`, `merge/ar-view`, `gap1-plan-endpoint`, `gap2-app-live-plan`,
  `gap4-pan-labels`, `gap5-e2e-script` are merged or superseded; delete after landing.
- Worktrees `HACKCMU2026-hull` (`hull-footprints`: convex-prism footprints in physics) and
  `HACKCMU2026-swift` (`swift-prisms`) belong to other sessions and are not on `main` yet;
  `docs/PHYSICS.md` v3 there describes code this branch does not have.
- Several sessions edit the main checkout at once; commit small and often, never `git stash`
  there (see `agent-memory/hackcmu-2026-test-quirks.md`).
