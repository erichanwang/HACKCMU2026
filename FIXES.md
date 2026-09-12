# FIXES

What still needs fixing, as of 2026-09-12 on branch `loop`. Ordered by how much it blocks the
demo. Each item names the file, what is wrong, and the fix. Every line below was re-verified
against the code on `loop` today (grep or read), not carried over from the morning version.

## 1. Blockers: the demo cannot run until this is done

- **The iOS app has never been built in Xcode.** `docs/AR_BUILD.md` is an honest checklist
  ("nobody on this team has compiled the iOS app... read it as 'should work from the code as it
  reads today,' not 'we've done this'"). On a Mac: `xcodegen generate`, pick a LiDAR device
  (Simulator can't run ARKit at all), expect real type errors the Linux `swiftc -parse` gate
  never caught. `tests/swift/typecheck/run.sh`'s own header says what it does and doesn't prove:
  internal consistency against hand-written shims, not that the shims match Apple's SDK, and
  "nothing actually runs — every shim body is `fatalError()`." One specific spot worth a second
  look on-device: `Spike/ScanView.swift:324-331`'s `nonisolated func session(...)` +
  `MainActor.assumeIsolated` for `ARSessionDelegate` on the `@MainActor` `Coordinator` — a
  reasoned pattern (comment explains why), untested against real ARKit delegate dispatch.

## 2. Bugs

- **The two packer3d wall-clock perf tests are still marked `slow` and excluded by default**
  (`packer3d/pyproject.toml:19-20`, `test_edge_cases.py:338`, `test_thorough_edge_cases.py:421`)
  even though today's decoder fix (7358c16) already fixed the thing they were waiting on: run
  both un-filtered (`pytest -m ''`) and they pass in 8.2s combined, nowhere near their 3s/15s
  budgets. `test_edge_cases.py:338`'s own comment ("unmark both when the decoder fix lands") is
  now stale — the fix landed, the unmark didn't. Drop both `@pytest.mark.slow` lines.

## 3. Risks that will bite in a real demo

- **`docs/DEMO_SCRIPT.md:15` claims `make server` matches the documented manual launch, but it
  doesn't.** The manual command in that same doc adds `--workers 2` (measured: one worker made a
  `GET /items` fired during `POST /plan` take 7.5x its solo latency; two workers brought it to
  0.6x — see `55c295c`'s commit message). `Makefile:26-31`'s `server:` target has no `--workers`
  flag. A teammate running `make server` today gets the stall the doc says is fixed. Add
  `--workers 2` to the Makefile, or stop claiming parity in the doc.
- **Demo plans are not reproducible run-to-run.** `server/planner.py:74` calls `pack_optimized`
  with `time_budget_s=per_run` only (never `max_iterations`), and `packer3d/packer3d/search.py`'s
  annealing loop exits on `time.perf_counter() - t0 >= budget_t` — so the same seed does a
  different amount of search, and can return a different plan, depending on machine load.
  `packer3d/ALGORITHM.md` section 5 already documents the fix (`time_budget_s=0,
  max_iterations=N` for a byte-identical rerun); nothing in the server/demo path uses it.
- **`scripts/ar_sim.py:1002`'s comment is now wrong.** It says the reality checks below it
  "never call `fail()`", but `lid_open_true_interior_check` (line 856, `fail()` at line 871,
  added in today's `59721c0`) does call it when escape exceeds 1cm. Not dangerous — the gate is
  stricter than the comment claims, not weaker — but confusing for whoever reads the comment
  next.
- **The suitcase wall-inset constants are still placeholders.** `suitcaseWallMeters`,
  `suitcaseFloorWallMeters`, `suitcaseHandleWallMeters` (`Spike/ScanView.swift:24-30`) are all
  1cm by default; they need a real bag to calibrate before the interior fit is trustworthy.

## 4. Missing versus the spec

- **Cavity nesting never reaches a real plan.** `packer3d/packer3d/models.py:573`'s `Placement`
  dataclass has no `nested_in` field and `to_dict()` never emits one, so
  `server/app_plan.py:57`'s `p.get("nested_in")` is always `None` for a real solver run — every
  real plan's `nestedIn` is `null`. Today's chain (`packing-core`'s nestedIn-aware overlap check,
  `ar_sim.py`'s nested decomposition) is real and correct, but only exercised by the hand-built
  `packing-core/.../nested-plan.json` fixture. The geometry side is already there —
  `from_scanned_heightmap` (models.py:258-303) carves a real cavity below 90% fill — the decoder
  just needs to record which item it put in whose cavity.
- **Folding exists but isn't used.** `physics/prepack.py:71`'s `fold_options` (flat / half-fold /
  rolled candidate boxes, real logic, tested in `tests/test_folding.py`) is computed for every
  soft item and attached to its scenario dict via `prepare_items` → `packable` — but nothing in
  `packer3d/packer3d/*.py` reads the `fold_options` key. The solver still only ever tries the
  item's single height-squashed box. (This morning's "no fold model exists" note was already
  out of date; the model exists, it's just not consulted.)
- **Editing Grok's guesses is still partial.** `Spike/SpikeApp.swift`'s `ItemEditor` has a label
  `TextField` (`:153`) and a rigidity `Picker` (`:158`); compressibility/mass/keepUpright (`:165`)
  are still `Text`-only, though `PATCH /items/{id}` (`server/main.py:305-311`) accepts all three.
- **PAN is still not in the app.** No `pan`/`PAN` reference anywhere in `Spike/*.swift`;
  rollouts exist only in `scripts/demo_e2e.py` and `python3 -m pan demo`. Say "physics-checked
  plan with an LLM risk read," not "imagine before you pack."

## 5. Stale docs

- `packer3d/README.md:15,286` and `ALGORITHM.md:404` say "104 tests"; `pytest --collect-only`
  finds 154 today (cavity/nesting/`verify_invariants` tests landed this morning and outgrew it).
- `scripts/README.md` documents `demo_e2e.py`'s outputs but omits `scenario.json`
  (`demo_e2e.py:165,197`), which the script both writes to `out/e2e` and reads back.
- `docs/INTEGRATION.md:121-136`'s CLI list (`validate`, `example`, `scan-to-object`) still omits
  `validate-packer3d` (`physics/__main__.py`), documented instead only in
  `docs/SOLVER_INTEGRATION.md` and `docs/PHYSICS.md`.

## 6. Branch and worktree hygiene

- `loop` is the integration branch; land on `main` only via a PR from it. Never rewrite or reset
  `loop`'s head — commit only on top, `git status` before every commit, never `git stash` here.
- The 09:00 branch audit is out of date: `git worktree list` now shows 92 active worktrees and
  `git branch` shows 82 local `worktree-agent-*` branches (98 total) — today's parallel-agent
  fleet, not the four branches the audit named. Don't mass-delete mid-event (some may still be
  live); this needs a cleanup pass once the demo is done.

## Fixed on `loop` today

Scan payload validation (422); Grok failures no longer lose the scan (background
`relabel_pending`, `labelStatus`, `?async=1`, polling `GET /items/{id}`); `DELETE`
routes for items/suitcases; the packer3d-adapter and Swift-port double-rotate bug
(`oriented=False`); the AR plan overlay, wall inset, label polling, Pack-button guard, items
sheet delete/reset, settings sheet with server-URL override; scan geometry trimming and its
Linux simulation; hull/convex-prism footprints; cavity-aware packing in packer3d (875137d) with
its `nestedIn` contract documented in `packing-core/CLAUDE.md` (see section 4 for what's still
missing); Auth0 open-mode fallback; Mongo fail-fast at startup; `make server` binding
`0.0.0.0`; the Grok prompt; PAN collapsed to one real backend + retry-once/lower-timeout
(`physics/pan.py`); hue-based color segmentation in `pan/evaluate.py`; the planner's global lock,
`unpacked`, and `runnerUp` fields; `scripts/pipeline_check.sh` plus the CI coverage for
`packer3d/tests` and `server/check.py`; README/MVP/IMPORTANT/OVERVIEW/PAN docs brought in line
with the actual stack.
