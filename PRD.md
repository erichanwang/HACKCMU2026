# HackCMU 2026 — Travel Track PRD: AR Packing Assistant

## Product vision and track focus

Help travelers fit their belongings into a suitcase and follow a practical packing plan before a trip. The app scans the bag and items, builds a metric 3D digital twin, computes a feasible arrangement, and guides physical placement with a 3D preview and AR.

**scan suitcase and travel items → reconstruct → pack → preview → follow AR guidance**

The primary user is a traveler packing carry-on luggage or working within a checked-bag limit. Students traveling to or from campus and travelers carrying fragile camera equipment are secondary users. Professional logistics and warehouse use are outside this hackathon's scope.

This document combines the spatial-packing demo requirements and the traveler-focused product requirements. It is the general product reference; `MVP.md` describes the demo implementation in more detail.

## Hackathon scope and release decisions

- Demo one rectangular suitcase or travel container with 4–6 rigid travel items. Start with synthetic cuboids, then replace them with measured oriented bounding boxes (OBBs).
- Capture with Swift and ARKit on a LiDAR-capable iPhone/iPad. Manual dimensions and corrections provide a fallback. Keep metric scale and a stable container anchor throughout the flow.
- Show a rotatable digital twin, compute collision-free placements, validate containment and support, animate the packing result, and guide at least one real placement with an AR ghost and manual **Placed** confirmation.
- Include a labeled layer view as a practical alternative to holding a phone during packing. AR is part of the HackCMU demo even though the longer-term validation roadmap below stages it after capture.
- Use a configurable measurement margin (initially 3–5%, to be calibrated), honor allowed orientations, and clearly report items that do not fit. Do not present a rejected or incomplete layout as a complete solution.
- The demo uses rigid objects and a rectangular interior. Voxel interiors, soft-item compression, nesting, multiple compartments, and production libraries are later work. No cloth simulation is required.
- Python/NumPy with optional PyBullet/Open3D can support prototype solving and physics validation. Fully offline, on-device solving is a later product target, not a claim about the demo.
- Weight, fragility, balance, and access priority motivate the travel product; implement them after the core demo unless capacity remains. Airline-rule lookup and packing-list generation are future scope.
- The requirement priorities and v0–v3 gates below describe the broader product roadmap. They do not expand the hackathon must-haves above. The ten-participant experiment is a product-validation gate, not a prerequisite to building the hackathon demo.

## Hackathon success criteria

A reliable demo completes within a few minutes: capture a suitcase and several travel items, inspect their geometry, generate a visibly valid arrangement, animate it, and physically place at least one item using aligned AR guidance. Global mathematical optimality is not required. Longer-term performance and utilization targets below are goals to validate, not measured results.

| | |
|---|---|
| **Status** | Draft |
| **Original travel PRD owner** | David Chung |
| **Platform** | iOS (iPhone Pro with depth sensor), iOS 17+ |
| **Target** | v0 validation build, then v1 public beta |

---

## 1. Problem

Packing a suitcase is a spatial optimization problem people solve badly, by hand, under time pressure. Travelers may repeatedly rearrange belongings and remain unsure whether everything fits or whether their bag is within a weight limit. The product focuses on physical fit and placement; baseline utilization, time savings, and competing solutions still need validation.

The same problem in professional contexts (equipment cases, field kits, fulfillment cartons) is solved manually by experienced people and costs real money when done poorly.

## 2. Goals and non-goals

### Goals
- G1. Produce a packing arrangement that measurably beats what the same user achieves by hand.
- G2. Produce plans that are always physically achievable. Never a plan that doesn't fit.
- G3. Make capture fast enough that the setup cost doesn't exceed the benefit.
- G4. Make the plan followable while both hands are busy.
- G5. Build a reusable item library so the second trip costs a fraction of the first.

### Non-goals (v0–v2)
- Not solving *what to bring* — packing-list generation is v3.
- Not simulating cloth physics. Deformables use a coarse compressibility model.
- Not supporting Android or non-depth iPhones in v1.
- Not a marketplace, social feature set, or travel booking integration.
- Not real-time continuous re-solve during packing. Re-solve is user-triggered.

## 3. Users

**Primary — the constrained traveler.** Flying carry-on-only or at the checked-bag weight limit. Motivated by a hard external constraint, not by tidiness. Packs 4–12 times a year.

**Secondary — the gear owner.** Camera, audio, drone, climbing, dive. Expensive fragile equipment, fixed cases, packs frequently, already thinks in terms of layouts. Highest willingness to pay, highest tolerance for scanning effort.

**Tertiary — the professional kit.** Field service, medical rep, trade show. Standardized loadout, repeated weekly, verification matters as much as fit.

## 4. Success metrics

| Metric | Target | Gate |
|---|---|---|
| Volume utilization vs. user's own hand-pack | +12 percentage points or better | **v0 gate** — if not met, stop |
| Plan feasibility (plans completed without an item failing to fit) | ≥ 95% | v2 ship gate |
| Time to first plan, first-ever session | < 8 minutes | v1 |
| Time to first plan, returning user with populated library | < 90 seconds | v1 |
| Solver latency, 30 items, on device | < 3 seconds p95 | v1 |
| Plan completion rate (user follows plan to the last item) | ≥ 60% | v2 |
| Items added to library per session (retention proxy) | ≥ 3 | v2 |

## 5. Requirements

### 5.1 Bag capture

| ID | Requirement | Priority |
|---|---|---|
| B1 | User can select a bag from a catalog of pre-scanned models and get a full interior mesh with wheel wells, handle rails, and lid compartment | P0 |
| B2 | User can scan an unknown bag via guided orbit; system fuses multiple depth frames and plane-fits walls and floor | P1 |
| B3 | User can define a bag by tapping four inner corners plus the top rim | P0 |
| B4 | Every captured bag is saved to a Bag Library and reusable without rescanning | P0 |
| B5 | System stores per-zone geometry: main cavity, lid pocket, side pockets, exterior compartments | P0 |
| B6 | System records soft-boundary metadata (expandable depth, shell rigidity) | P2 |
| B7 | User can correct any captured dimension by hand before solving | P0 |

### 5.2 Item capture

| ID | Requirement | Priority |
|---|---|---|
| I1 | User can capture multiple items in one pass from a flat surface; system segments each and fits an oriented bounding box | P0 |
| I2 | User can enter an item by typed dimensions | P0 |
| I3 | User can add a packaged product by barcode, pulling dimensions from a product database | P1 |
| I4 | User can run full photogrammetry on irregular high-value items | P2 |
| I5 | Every item is saved to a persistent Item Library, editable and reusable | P0 |
| I6 | User can tag rigidity, fragility, orientation lock, and access priority per item; system pre-fills defaults by category | P0 |
| I7 | User can mark an item as a container with usable interior volume (shoes, pots) | P1 |
| I8 | Items can be grouped into reusable sets ("camera kit", "gym bag") | P2 |

### 5.3 Solver

| ID | Requirement | Priority |
|---|---|---|
| S1 | Produces an ordered placement list (item, position, orientation, zone) for a given bag and item set | P0 |
| S2 | Inflates every measured dimension by a configurable tolerance (default 3–5%) before solving | P0 |
| S3 | Respects orientation locks | P0 |
| S4 | Enforces a total weight cap and reports estimated total weight | P1 |
| S5 | Optimizes center of mass toward the wheel end for upright stability | P1 |
| S6 | Places fragile items adjacent to soft items where possible | P1 |
| S7 | Keeps high access-priority items in the top layer or lid pocket | P1 |
| S8 | Nests small items inside container items before main placement | P1 |
| S9 | Assigns soft items to remaining voids with compression as a final pass | P0 |
| S10 | Re-solves from a user-reported partial state and re-plans only the remainder | P0 (v3) |
| S11 | Reports leftover items that do not fit, ranked by what to drop first | P0 |
| S12 | Runs fully on device with no network dependency | P1 |
| S13 | Returns in under 3 seconds p95 for 30 items | P1 |

### 5.4 Guidance

| ID | Requirement | Priority |
|---|---|---|
| G1 | 2D layer view: exploded diagram by layer (bottom, middle, top, lid pocket) with item labels | P0 |
| G2 | AR step mode: current item rendered solid in position, placed items dimmed, future items hidden | P0 (v2) |
| G3 | AR uses scene-depth occlusion so real contents correctly hide virtual items | P0 (v2) |
| G4 | Anchor persists across app backgrounding within a session | P1 |
| G5 | Manual anchor fallback by tapping two inner corners | P0 (v2) |
| G6 | Step counter and plain-language placement description for every step | P0 |
| G7 | User can mark a step "didn't fit" and trigger a re-solve | P1 |
| G8 | Auto-advance by depth-diff confirmation, user-disableable | P2 |
| G9 | Plan is exportable or shareable as a static image | P2 |

### 5.5 Non-functional

| ID | Requirement |
|---|---|
| N1 | All capture data stored locally by default; cloud sync opt-in |
| N2 | App is fully functional offline except catalog and barcode lookup |
| N3 | Full capture-to-plan session drains no more than ~8% battery |
| N4 | No account required to reach a first plan |
| N5 | Accessible layer view for users who can't or won't use AR |

## 6. Key design decisions

- **OBBs, not meshes.** Use an oriented bounding box plus a class label as the initial solver representation; validate its adequacy on real travel items. Full meshes are reserved for high-value irregular items. This is what makes capture fast enough to be worth doing.
- **Voxel container, not a prism.** Wheel wells, handle rails, and lid compartments require richer geometry in the broader product; the hackathon begins with a rectangular interior.
- **Tolerance inflation is mandatory, not optional.** One infeasible plan permanently ends the user relationship. Bias toward roomy.
- **Soft items solved last.** They're the only class that adapts, so they absorb the error left by everything else.
- **The 2D layer view is the product; AR is the demo.** Hands are busy while packing. Both ship together, and the layer view is never gated behind AR.

## 7. Scope by release

**v0 — validation (~2 weeks, internal).** No AR, no depth capture. Manual bag dimensions or catalog pick. Items typed or roughly photographed. Solver plus 2D layer diagram. Sole purpose: run the utilization experiment.

**v1 — capture and library.** Depth bag scanning, multi-item photo capture, Bag Library and Item Library, weight estimation, layer view.

**v2 — AR.** Step mode, depth occlusion, world-map persistence, manual anchor fallback.

**v3 — retention.** Re-solve from partial state, airline size and weight rule checking, packing-list generation from the library, item sets.

## 8. Validation plan

For the broader product, run the v0 validation gate before investing in production app development:

1. Recruit ten participants with their own suitcases and a fixed item set.
2. Have each pack by hand, no guidance. Record time and measure achieved volume utilization.
3. Unpack. Generate a plan with the v0 solver. Have them repack following the layer diagram.
4. Record time, utilization, number of deviations from the plan, and whether any item failed to fit.

Ship criteria: median utilization gain ≥ 12 points, zero infeasible plans across all ten. If either fails, the geometry model or the tolerance strategy is wrong and no amount of interface work fixes it.

## 9. Open questions

- What compressibility factor `k` actually holds for common garment types? Needs empirical measurement, not a guess.
- Is segmentation accurate enough on a cluttered table, or does capture need a one-item-at-a-time flow in v1?
- How many bag models must the catalog cover before the guided-scan path becomes a rarely used fallback?
- Does depth-diff auto-advance work reliably enough to ship, or does it break trust when it misfires?
- Does the item library survive contact with reality, or do people's possessions change faster than the library pays off?
- Which vertical (foam layout, field kits, carton selection) should get a dedicated build first, and does it need a different interface entirely?

## Hackathon capture, viewer, and execution requirements

Capture container dimensions, pose, origin, orientation, and optional mesh; capture each object's stable ID, dimensions, observed position, and quaternion rotation. Optional mesh URLs must not be required by the solver. The container defines the allowed packing volume.

The viewer shows the container, objects, labels, observed transforms, and packed transforms. Support orbit, zoom, pan, and selection; allow switching between scan geometry and packing proxies when both exist. Animate each object into its planned pose inside a transparent container.

The solver sorts larger items first, enumerates allowed orientations and candidate points, rejects intersections, and scores remaining space, occupied height, and fragmented free space. Return an ordered placement list and explicit leftovers if a full arrangement is infeasible.

Physics validation checks collisions, container bounds, gravity, support, and gross instability before a layout is accepted. Begin with synthetic cuboids so capture, solver, validation, and rendering can develop independently.

For AR, anchor the target transforms to the real suitcase, highlight the current item, show its orientation and short movement instructions, dim placed items, and hide future items. The user confirms placement manually. Automatic pose verification, scan-confidence overlays with rescan prompts, mesh-aware refinement, and choosing among multiple containers are stretch work.

## System Architecture

```text
          iPhone / iPad
                │
         RGB + LiDAR + ARKit
                │
                ▼
       Spatial Reconstruction
                │
                ▼
         Shared Scene Format
                │
        ┌───────┴─────────┐
        │                 │
        ▼                 ▼
 Packing Algorithm    Physics Engine
        │                 │
        └───────┬─────────┘
                │
                ▼
           Placements
                │
        ┌───────┴─────────┐
        ▼                 ▼
   3D Simulation       AR Guide
```

## Team Ownership

### Person 1 — iOS / LiDAR / Capture

Responsibilities:
- ARKit configuration
- LiDAR scene reconstruction
- object/container scanning
- camera pose
- geometry extraction
- scene export

Required output:
- container geometry
- object geometry
- world transform

### Person 2 — 3D Reconstruction / Rendering / AR

Responsibilities:
- interactive 3D scene
- mesh rendering
- transparent container
- packing animation
- ghost placements
- AR visualization

Consumes:
- objects
- container
- placements

This subsystem should first work with hard-coded objects.

### Person 3 — Packing Algorithm

Responsibilities:

```text
solve(container, objects) → placements
```

Initial implementation:
- sort objects largest first
- enumerate useful orientations
- generate candidate placement points
- reject intersections
- score candidate layouts

Future:
- convex-hull packing
- local search
- simulated annealing / heuristic refinement
- fragmented-space penalties

### Person 4 — Physics / Validation

Responsibilities:

```text
validate(scene, placements)
```

Validation:
- collision
- container bounds
- gravity
- support
- stability

Suggested stack:
- Python
- NumPy
- Open3D
- PyBullet

The physics module should initially operate entirely on synthetic cuboids so development is independent of LiDAR progress.

## Shared Data Contract

### Units

**Meters only.**

### Coordinate system

All components must share one documented coordinate convention.

Example:

```text
X = right
Y = up
Z = forward
```

### Rotation

Use quaternions:

```text
[x, y, z, w]
```

### IDs

Every object must maintain the same unique ID across:
- scan
- solver
- physics
- renderer
- AR

## Integration Strategy

### Stage 1: mocked scene

Build:
- fake container
- five fake cuboids

Verify:

```text
solver
→ physics
→ renderer
```

### Stage 2: LiDAR geometry

Swap synthetic geometry for real measured OBBs.

### Stage 3: AR

Feed final transforms into the AR placement system.

### Stage 4: refinement

Only after end-to-end integration:
- mesh-aware collision
- improved solver
- automatic placement checks
- scan quality visualization
- UI polish

## Technical Principles

### Decouple perception from packing

The solver should not care whether dimensions came from:
- LiDAR
- a mock file
- manual test data

### Decouple packing from rendering

The renderer should consume transforms rather than depend on solver internals.

### Preserve metric geometry

All components must preserve:
- scale
- position
- orientation

### Prefer an end-to-end demo over perfect subcomponents

A complete loop with rough geometry is more valuable than perfect reconstruction with no working AR execution.

## Travel-track demo script

1. Show an empty suitcase beside a small set of travel items: a toiletry case, shoes, a charger case, and other rigid belongings.
2. Explain the travel problem: knowing what to bring does not tell you how it fits.
3. Scan the suitcase and items, then inspect the metric digital twin.
4. Tap **Pack** and show the validated layout animate into the suitcase. Surface any leftovers explicitly.
5. Inspect the layer view, switch to AR, and place the first item using its ghost and orientation guide.
6. Close with the travel benefit: a packing plan the traveler can actually follow before leaving for a trip.
