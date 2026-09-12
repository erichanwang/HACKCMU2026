# PRD — AR Packing Assistant

| | |
|---|---|
| **Status** | Draft |
| **Owner** | David Chung |
| **Platform** | iOS (iPhone Pro with depth sensor), iOS 17+ |
| **Target** | v0 validation build, then v1 public beta |

---

## 1. Problem

Packing a suitcase is a spatial optimization problem people solve badly, by hand, under time pressure. The typical traveler achieves roughly 60–70% volume utilization, repacks two or three times, and still arrives at the airport unsure whether the bag is over the weight limit. Nothing existing solves this — packing cubes reorganize the problem without solving it, and packing-list apps handle *what* to bring, not *how it fits*.

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

- **OBBs, not meshes.** For roughly 95% of objects, an oriented bounding box plus a class label is sufficient input to the solver. Full meshes are reserved for high-value irregular items. This is what makes capture fast enough to be worth doing.
- **Voxel container, not a prism.** Wheel wells, handle rails, and the lid compartment are 10–15% of usable volume and all of the awkward corners.
- **Tolerance inflation is mandatory, not optional.** One infeasible plan permanently ends the user relationship. Bias toward roomy.
- **Soft items solved last.** They're the only class that adapts, so they absorb the error left by everything else.
- **The 2D layer view is the product; AR is the demo.** Hands are busy while packing. Both ship together, and the layer view is never gated behind AR.

## 7. Scope by release

**v0 — validation (~2 weeks, internal).** No AR, no depth capture. Manual bag dimensions or catalog pick. Items typed or roughly photographed. Solver plus 2D layer diagram. Sole purpose: run the utilization experiment.

**v1 — capture and library.** Depth bag scanning, multi-item photo capture, Bag Library and Item Library, weight estimation, layer view.

**v2 — AR.** Step mode, depth occlusion, world-map persistence, manual anchor fallback.

**v3 — retention.** Re-solve from partial state, airline size and weight rule checking, packing-list generation from the library, item sets.

## 8. Validation plan

The v0 gate runs before any app code:

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
