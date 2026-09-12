# HackCMU 2026 — PRD: AR Spatial Packing

## Product Vision

Build an iOS application that converts a simple spatial scan into an executable physical packing plan.

The user scans a real set of objects and a container using an iPhone/iPad camera + LiDAR. The system creates a 3D digital twin, computes a packing arrangement, visualizes the solution in 3D, and projects the result back into the physical world through AR.

Core idea:

**scan reality → reconstruct digital twin → compute packing → simulate in 3D → execute in AR**

## Problem

Traditional packing algorithms assume object geometry is already known.

Real users instead have to:
- manually measure objects
- estimate whether items will fit
- repeatedly rearrange objects
- mentally translate a theoretical packing plan into physical placement

The product removes those assumptions by perceiving the objects directly and closing the loop between computation and the physical world.

## Target Users

Initial users:
- travelers packing luggage
- students moving into/out of dorms
- people packing storage bins
- warehouse or logistics workers
- anyone loading irregular objects into constrained physical space

Hackathon demo target:
- a suitcase, box, or storage container
- 4–8 household objects

## Core User Journey

1. User opens the app.
2. User scans the empty container.
3. User scans the objects around it.
4. The app reconstructs the scene in 3D.
5. User reviews the detected object models.
6. User taps **Pack**.
7. The system computes a feasible arrangement.
8. The app animates the result in the 3D viewer.
9. User switches to AR mode.
10. The app guides physical placement object-by-object.
11. User finishes with the real container matching the digital plan.

## Product Requirements

### PR-1: Spatial capture

The application must capture:
- container geometry
- object geometry
- object pose
- metric scale
- world-coordinate alignment

Preferred stack:
- iOS
- Swift
- ARKit
- LiDAR-capable device

### PR-2: Object representation

Each object should have:

```json
{
  "id": "shoe_01",
  "position": [0.2, 0.1, -0.4],
  "rotation": [0, 0, 0, 1],
  "dimensions": [0.28, 0.11, 0.10],
  "mesh_url": "shoe.glb"
}
```

For MVP:
- oriented bounding box is sufficient

Future:
- convex hull
- triangle mesh
- confidence map
- semantic class

### PR-3: Container representation

Container must include:
- dimensions
- pose
- origin
- orientation
- optional mesh

The solver treats it as the allowed packing volume.

### PR-4: 3D digital twin

The application must render:
- container
- all detected objects
- positions and orientations
- packed result

Viewer interactions:
- orbit
- zoom
- pan
- select object
- toggle labels
- toggle raw scan vs packing proxy

### PR-5: Packing computation

The packing subsystem must return a target transform for every object.

Interface:

```text
solve(container, objects) → placements
```

Placement:

```json
{
  "id": "shoe_01",
  "target_position": [0.13, 0.05, 0.08],
  "target_rotation": [0, 0.707, 0, 0.707]
}
```

Constraints:
- no object intersections
- all objects within the container
- allowed object rotations
- arrangement should use space reasonably efficiently

Potential scoring function:

\[
score =
\alpha(\text{unused volume})
+
\beta(\text{maximum occupied height})
+
\gamma(\text{fragmented free space})
\]

Global optimality is not required for HackCMU.

### PR-6: Physics validation

Candidate layouts should be validated for:
- collision
- containment
- gravity
- support
- basic stability

Suggested tools:
- PyBullet
- NumPy
- Open3D

Pipeline:

```text
packing candidate
↓
collision validation
↓
stability validation
↓
accept / reject
```

### PR-7: 3D packing simulation

After a valid solution is found:
- animate each object into its target pose
- show final layout inside a transparent container
- allow the user to inspect the result from any angle

The simulation serves as both:
- explanation of the algorithm
- preview of the physical task

### PR-8: AR execution

The application must anchor the planned layout to the real container.

For each object:
1. show a translucent ghost at the target location
2. show orientation
3. optionally show translation/rotation hints
4. allow user to confirm placement
5. advance to the next object

Example:

```text
Object 3
Move 4 cm left
Rotate 15° clockwise
```

## Stretch Requirements

### SR-1: Mesh-aware refinement

Use:
- OBBs for fast search
- convex hulls / simplified meshes for final validation

Pipeline:

```text
OBB search
↓
candidate layouts
↓
mesh collision checks
↓
refined solution
```

### SR-2: Automatic placement verification

After a physical object is placed, compare its observed pose with its target pose.

Possible feedback:

```text
✓ correct

or

Move 3 cm forward
Rotate 12° clockwise
```

### SR-3: Scan confidence

Visualize uncertain geometry:
- green = well observed
- yellow = incomplete
- red = missing

The app may ask the user to rescan a region.

### SR-4: Multiple containers

Allow scanning several containers and selecting the best one automatically.

### SR-5: Packing proxies

Toggle among:
- real reconstructed mesh
- packing proxy
- final packed arrangement

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

## Success Metrics

For HackCMU, success means:
- scan-to-model loop works
- packing solution is visibly valid
- 3D simulation is understandable
- AR overlay is spatially aligned
- user can physically place at least one item using the generated guidance
- demo completes reliably within a few minutes

## Demo Script

Start with a pile of objects beside an empty container.

> Traditional packing algorithms assume someone already measured every object. We remove that assumption.

Scan the scene.

> From one spatial scan, we build a metric 3D digital twin of the objects and container.

Show the reconstructed scene.

Press **Pack**.

> We compute a feasible arrangement against the geometry we observed and validate that it is physically realizable.

Show the animated packed configuration.

Switch to AR.

> A computational solution is useless if the user has to mentally recreate it, so we project the solution back into the real world.

Place the first object using the ghost overlay.

Closing line:

> We turn physical space into a solvable representation, then turn the solution back into physical action.
