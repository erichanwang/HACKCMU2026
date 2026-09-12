# AR loop prompt

Feed this whole file back to yourself every iteration. Branch `loop`. Nothing is pushed.

## Goal

`bash scripts/ar_gate.sh` prints `AR GATE: GREEN` **and** every deliverable D1–D5 below is
done. Green with a gate that checks nothing is failure — each iteration the gate must cover
more of the AR path than the last, until it covers all of D1–D5.

Right now it is RED: `tests/main.swift:31: Assertion failed: grid 21x11` — a 0.20 m box
yields 21 heightmap cells instead of 20 (float rounding in `heightMap`, `Spike/Geometry.swift`).

## Each iteration

1. `bash scripts/ar_gate.sh 2>&1 | tail -40`. Write down exactly what is red.
2. Green **and** D1–D5 all done → `ScheduleWakeup(stop: true)`, then report to Eric: what
   works, what still needs a Mac, the commands he runs to see it. Stop.
3. Otherwise dispatch the agents below whose deliverable is not done — **all in one message,
   `subagent_type: "general-purpose"`, `model: "sonnet"`, at least 4 of them.**
4. Agents return → re-run the gate → commit on `loop`, message naming what got fixed.
5. Two consecutive iterations with no gate change and no new coverage → stop and report the
   blocker instead of spinning.

## Deliverables

- **D1 scan geometry is correct.** `Spike/Geometry.swift` — `heightMap` cell count, `fitBox`,
  `minAreaRect`, `connectedCluster`, `densify` — right on synthetic scenes: axis-aligned and
  rotated boxes, an L-shape, a cylinder, two objects 10 cm apart, a single-point degenerate
  scan, a box whose size is an exact multiple of the cell. No assertion may be loosened to
  pass; fix the code, not the test.
- **D2 AR plan overlay.** `Spike/PlanAnchor.swift`, new, **no ARKit/RealityKit import** so it
  compiles under `swiftc` on Linux: bag frame → world transform per
  `docs/superpowers/specs/2026-09-12-pipeline-loop-design.md` ("Bag frame → AR world"). Origin
  = bag interior min corner, bag X = scanned `BoxFit.axis`, Y = world up, Z = `(-axis.z, 0, axis.x)`.
  Tests: every placement of a real solver plan lands inside the scanned bag, a 90°-rotated bag
  still lands inside, round-trip world→bag→world is identity within 1 mm.
- **D3 app wiring.** `Spike/API.swift` LAN IP is not hard-coded (env/Info.plist/UserDefaults —
  pick one, keep it small). `Spike/ScanView.swift` + `SpikeApp.swift`: poll `GET /items/{id}`
  while `labelStatus == "pending"`, draw the D2 overlay after a successful Pack, disable Pack
  while a plan is running. These files import ARKit and cannot compile on Linux — so keep every
  bit of logic worth testing in pure files that can, and say in the report what only a Mac can check.
- **D4 headless pipeline sim.** `scripts/ar_sim.py`: synthesise a LiDAR-ish point cloud for a
  suitcase and 3–5 items, drive the real scan geometry (via the swift binary the scan run.sh
  builds, or a faithful Python port — your call, say which), `POST` them to a locally started
  server, `POST /plan`, push the placements through the D2 transform, assert every item is
  inside the bag and no two overlap. Must run with no Mac, no phone, no API keys — skip Grok
  labelling when `XAI_API_KEY` is unset, and spin up Mongo itself or skip cleanly with a
  message if it cannot.
- **D5 Mac build honesty.** `docs/AR_BUILD.md`: the exact commands a teammate runs on a Mac
  (`xcodegen generate`, open, run), what the gate already proved on Linux, and the list of
  things only the device can prove. Fix `project.yml` if it does not reference the new files.

## Agents

One agent per row, files disjoint — no two agents touch the same file in one iteration.

| Agent | Deliverable | Owns |
|---|---|---|
| A1 scan-geometry | D1 | `Spike/Geometry.swift`, `tests/main.swift`, `tests/swift/scan/**`, `tests/swift/SimdShim.swift` |
| A2 ar-overlay | D2 | `Spike/PlanAnchor.swift`, `tests/swift/plan/**` |
| A3 app-wiring | D3 | `Spike/API.swift`, `Spike/ScanView.swift`, `Spike/SpikeApp.swift` |
| A4 pipeline-sim | D4 | `scripts/ar_sim.py`, `scripts/README.md` |
| A5 gate-and-build | D5 | `scripts/ar_gate.sh`, `docs/AR_BUILD.md`, `project.yml`, `Makefile`, `.github/workflows/ci.yml` |

## Every agent prompt must carry

- The failing command and its output. Fix the root cause, not the assertion.
- Test first: add the red check, then make it pass. A new `tests/swift/<dir>/run.sh` is picked
  up by the gate automatically; model it on `tests/swift/scan/run.sh` (sources
  `swift/PackPhysics/swiftenv.sh`, `swiftc -Onone`, exits non-zero on failure).
- Ponytail: smallest diff that works, stdlib only, no new dependencies, no abstraction with one
  caller. `swiftc -Onone` — top-level code under `-O` trips a spurious exclusivity check.
- Touch only the files in your row. If you need a change elsewhere, report it, do not make it.
- Run your own check before reporting. Report: files changed, the one command that verifies it,
  its actual output, and anything you could not prove on Linux. Do not claim green without
  pasting the output.
- Do not commit, do not push, do not create worktrees. The session commits.

## Rules

- Branch `loop` only. Never push, never force-push (`main` was force-pushed once already).
- Do not touch `../HACKCMU2026-hull`, `-swift`, `-render`, `-integrate` — other sessions own them.
- Commit small and often in this checkout; never `git stash` here.
- No `Co-authored-by` trailers.

## Ownership split (added 2026-09-12 08:10, after a collision)

Session `hackcmu2026-16` is running its own five agents in worktrees under `.claude/worktrees`
against the same design spec, and merges them into `loop`. It owns **D1, D2, D3** — that is
`Spike/**` (Geometry, PlanAnchor, ScanView, SpikeApp, API), `tests/swift/scan/**`,
`tests/swift/plan/**`, `tests/main.swift`, `server/main.py`, `server/check.py`, `SCAN_OUTPUT.md`,
`physics/packer3d_adapter.py`, `scripts/demo_e2e.py`, `scripts/pipeline_check.sh`, `Makefile`,
`.github/workflows/ci.yml`, `scripts/README.md`, `packer3d/**`.

**This session owns only: `scripts/ar_sim.py`, `scripts/ar_gate.sh`, `docs/AR_BUILD.md`,
`project.yml`.** Do not dispatch an agent against anything else while that session is live —
message it first (`SendMessage` to `uds:/run/user/1000/cc-socks/546545.sock`). Every iteration,
check whether it is still running before fanning out; when it is done, the rest of D1–D5 is
verification, not reimplementation.

`Spike/PlanAnchor.swift` contract as landed by that session (use it from `scripts/ar_sim.py`):

    interiorBox(_ outer: BoxFit, wall: Float) -> BoxFit
        // width/depth minus 2*wall, height minus wall, centre raised wall/2
    struct PlanAnchor {
        init(interior: BoxFit, planeY: Float)   // planeY = table + suitcaseWallMeters
        let axis, perp, origin: SIMD3<Float>    // perp = (-axis.z, 0, axis.x)
        func worldCenter(position: SIMD3<Float>, size: SIMD3<Float>) -> SIMD3<Float>
    }
    // origin = (center.x, planeY, center.z) - axis*width/2 - perp*depth/2
    // worldCenter = origin + axis*(p.x+s.x/2) + up*(p.y+s.y/2) + perp*(p.z+s.z/2)

`tests/swift/plan/run.sh` is the reference check.

### Open question for Eric

`server/auth.py` has an uncommitted `open_mode()` — when `AUTH0_DOMAIN`/`AUTH0_AUDIENCE` are
unset, `require_auth` returns `{"sub": "local"}` instead of 401, which disables the auth added in
`ba42ae5`. Written by the pipeline-sim agent, which was out of its lane. The iOS app has no Auth0
login flow, so with auth on and no token every upload from the phone 401s. Do not commit it
without Eric's call.
