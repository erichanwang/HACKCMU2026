# packing-core

The Swift package that consumes a packing plan and shows it. Plan models, decoding and
structural validation, geometry and stability checks, `PlanStats`, the 2D layer diagram and
the 3D orbit scene. It does **not** solve packing — the solver (`packer3d/`) does, and the
plan JSON is the contract between them.

Read [`CLAUDE.md`](CLAUDE.md) first: units, the coordinate frame, the min-corner rule, the
plan JSON schema and nested placements. Those conventions are not restated here. The one to
carry into any rendering work is `center = position + size / 2`.

## Two targets, and why

| Target | Imports | Holds |
|---|---|---|
| `PackingPlan` | Foundation only | `PackingPlan`, `PlanLoader`, `PlanGeometry`, `PlanStats`, `PlanProjection`, `AxisRotation`, `Resources/plan.json` |
| `PackingPlanUI` | SwiftUI (and CoreGraphics where it exists) | `PlanViewer`, `PlanDiagramView`, `PlanSceneView`, `PlanLayer`, `FootprintProjection`, `PlanPresentation` |

`PackingPlan` is deliberately free of SwiftUI, ARKit and RealityKit, so the plan maths is
testable on a host with no simulator and no camera. `PackingPlanUI` holds the fallback
rendering: it imports SwiftUI but still no ARKit. **AR code belongs in the app target, not
here.** `Package.swift` says some of this in comments — read it.

The split is load-bearing for the projection too: `PlanProjection` (the orthographic camera
that returns back-to-front `[ProjectedBox]`) lives in `PackingPlan`, not in the UI target,
which is what lets `tools/plan3d` render the 3D view on Linux.

## Build and test it on Linux

`swift test` runs here, and the whole suite is green. The count moves most days, so
this does not pin it — `tools/doc_check` would only ever be catching the doc up:

```sh
source swift/PackPhysics/swiftenv.sh      # from the repo root: Swift 6.3.3 via swiftly + libxml2 compat
cd packing-core
swift test --scratch-path /tmp/pc-build
```

```
Test Suite 'All tests' passed at ...
	 Executed <n> tests, with 0 failures (0 unexpected) in 0.375 (0.375) seconds
```

It did not run until today. `PackingPlanUI` imported `CoreGraphics` and `SwiftUI`
unguarded, and SwiftPM builds the whole package graph, so one unavailable module took the
pure-geometry tests down with it. Both are behind `#if canImport(...)` now. No shim was
needed for the geometry types — Linux Foundation supplies real `CGFloat`/`CGPoint`/`CGRect`
with the same API, so `FootprintProjection` just falls back to `import Foundation`.

### Trap 1: `--product` is not `--target`

```sh
swift build --product PackingPlan
```

```
Building for debugging...
warning: '--product' cannot be used with the automatic product 'PackingPlan'; building the default target instead
...
[16/22] Compiling PackingPlanUI PlanSceneView.swift
[17/22] Compiling PackingPlanUI PlanViewer.swift
...
Build of product 'PackingPlan' complete! (15.92s)
```

Both libraries are *automatic* products, so `--product` is ignored: SwiftPM warns once and
builds the default target instead, dragging `PackingPlanUI` in. Two agents lost time to this
today, when the same command still ended in a `CoreGraphics` error that read like the
platform guard had failed. It doesn't error any more — but it also isn't building what you
asked for, and the last line still says `product 'PackingPlan' complete`. Use `--target`:

```sh
swift build --target PackingPlan --scratch-path /tmp/pc-target
```

```
[12/13] Compiling PackingPlan PackingPlan.swift
[13/13] Emitting module PackingPlan
Build of target: 'PackingPlan' complete! (17.46s)
```

Nothing from `PackingPlanUI` in the log. That is the check that the pure-geometry target is
still pure.

### Trap 2: `.build` is shared

Several agents work in this package at once and `.build` is contended. Pass
`--scratch-path <dir>` — every command above does. And
`error: input file was modified during the build` means someone edited a file mid-build, not
a real failure: rerun it.

## Looking at a plan without a Mac

`tools/plan3d` renders a plan to SVG through this package — the same
`projected(camera:size:upTo:)` the SwiftUI scene calls, so if the SVG is wrong the
projection is wrong. It has caught real bugs by eye.

```sh
cd tools/plan3d
swift build --scratch-path /tmp/plan3d
/tmp/plan3d/debug/plan3d ../../packing-core/Sources/PackingPlan/Resources/plan.json /tmp/svg
```

```
plan: 6 placements, container (0.406, 0.152, 0.610), geometry issues: none
nesting: no placement declares nestedIn — no cavities drawn, every item a plain solid
three-quarter.svg  boxes=7 container-first=yes bbox=(36.0,54.6)-(864.0,645.4)
front.svg          boxes=7 container-first=yes bbox=(36.0,194.8)-(864.0,505.2)
top.svg            boxes=7 container-first=yes bbox=(157.4,28.0)-(742.6,672.0)
```

Flags (`--steps`, `--cutaway`, `--unpacked`, `--violations`), what the per-file line catches
and what the header band on each SVG means: [`docs/PLAN_3D.md`](../docs/PLAN_3D.md). Don't
commit the SVGs.

## The bundled demo plan

`Sources/PackingPlan/Resources/plan.json` is the hand-authored demo carry-on (0.4064 ×
0.1524 × 0.6096 m, six items, three zones — `base` and `upper` overlap in Y, see
CLAUDE.md). Every `#Preview` renders it,
`PlanLoader.mockPlan()` loads it, and the tests assert it is both geometrically clean and
free of stability issues (`testMockPlanIsCleanAndStable`). That assertion is load-bearing:
until today the shirts and the laptop floated in mid-air in it. If you edit the plan, that
test is the gate.

## What this package still needs from a Mac

`PlanViewer` is the view an app should present: a `Layers | 3D` segmented picker hosting
`PlanDiagramView` and `PlanSceneView` over the same plan. The app presents
`PlanDiagramView` directly instead, at `Spike/SpikeApp.swift:75`:

```swift
if let plan { PlanDiagramView(plan: plan) }   // → PlanViewer(plan: plan)
```

That one line is the whole change, and it lives in the app target, which belongs to another
session — so it is written down here rather than made.

Nothing in `PackingPlanUI` can be *seen* on Linux; the SwiftUI files vanish behind
`canImport`. On a Mac, open these `#Preview`s:

| Preview | View |
|---|---|
| Demo carry-on — layers · Demo carry-on — 3D | `PlanViewer` — both modes |
| Demo carry-on — base layer · Demo carry-on — upper layer | `PlanDiagramView` (upper layer shows the lower items as dashed outlines) |
| Demo carry-on — empty bag · Demo carry-on — fully packed | `PlanSceneView` (the packed one is the only preview with something to tap) |

AR itself is neither here nor in the simulator: see [`docs/AR_BUILD.md`](../docs/AR_BUILD.md).
