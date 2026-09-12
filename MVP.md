# HackCMU 2026 — MVP: AR Spatial Packing

## Goal

Build an iOS application that lets a user scan a set of physical objects and a container, reconstructs them in 3D, computes a packing arrangement, and guides the user through recreating that arrangement in AR.

Core loop:

**scan reality → reconstruct digital twin → compute packing → simulate in 3D → execute in AR**

## MVP Demo Flow

1. User scans one container and 4–6 objects with an iPhone/iPad.
2. App reconstructs each object and the container as basic 3D geometry.
3. App displays a rotatable 3D scene.
4. User presses **Pack**.
5. Packing solver returns collision-free poses for each object.
6. The 3D scene animates objects into their target positions.
7. User enters AR mode.
8. App overlays a ghost of the first object in the correct physical location.
9. User places it and advances to the next object.

## Must-Have Features

### 1. Spatial scan

Input:
- one rectangular container
- 4–6 physical objects

Minimum output per object:

```json
{
  "id": "object_1",
  "position": [0.2, 0.1, -0.4],
  "rotation": [0, 0, 0, 1],
  "dimensions": [0.28, 0.11, 0.10]
}
```

Minimum acceptable geometry:
- oriented bounding box

Stretch:
- convex hull
- reconstructed mesh

### 2. 3D digital twin

Show:
- container
- scanned objects
- dimensions
- object labels

Modes:
- Scan
- 3D Model
- Pack
- AR Guide

### 3. Packing solver

Input:

```json
{
  "container": {
    "dimensions": [0.55, 0.35, 0.22]
  },
  "objects": [
    {
      "id": "object_1",
      "dimensions": [0.28, 0.11, 0.10]
    }
  ]
}
```

Output:

```json
{
  "placements": [
    {
      "id": "object_1",
      "position": [0.13, 0.05, 0.08],
      "rotation": [0, 0.707, 0, 0.707]
    }
  ]
}
```

Requirements:
- all objects remain inside the container
- no intersections
- rotations supported
- reasonably dense arrangement

Global mathematical optimality is not required for the MVP.

### 4. Physics / validation

Validate:
- object-object collisions
- container bounds
- unsupported floating objects
- grossly unstable placements

MVP assumption:
- rigid objects only
- no deformable-body simulation

### 5. 3D packing simulation

After the solver finishes:
- animate objects from scanned positions into packed positions
- make container semi-transparent
- allow camera rotation around final arrangement

### 6. AR placement guide

Overlay the target object pose into the real container.

Example instructions:

```text
Place Object 1
Rotate clockwise
Move 4 cm left
```

For MVP, the user can manually press **Placed ✓**.

Automatic pose verification is stretch.

## Team Split

### Person 1 — iOS / LiDAR
Owns:
- ARKit session
- LiDAR scan
- camera pose
- container/object geometry extraction

### Person 2 — 3D rendering / AR
Owns:
- digital twin viewer
- meshes
- transparent container
- packing animation
- AR ghost placement

### Person 3 — packing algorithm
Owns:

```text
solve(container, objects) → placements
```

Initial approach:
- largest objects first
- orientation permutations
- candidate placement points
- intersection checks
- packing-density scoring

### Person 4 — physics / validation
Owns:

```text
validate(scene, placements)
```

Checks:
- collisions
- containment
- support
- stability

Suggested stack:
- Python
- NumPy
- Open3D
- PyBullet

## Shared Integration Contract

### Units

Use **meters everywhere**.

### Coordinate convention

Pick one convention and document it. Example:

```text
X = right
Y = up
Z = forward
```

### Placement format

```json
{
  "id": "shoe_01",
  "target_position": [0.13, 0.05, 0.08],
  "target_rotation": [0, 0.707, 0, 0.707]
}
```

## Development Order

### Phase 1 — fake everything

Before real LiDAR integration:
- create one fake container
- create five fake cuboids
- verify solver → physics → renderer end-to-end

### Phase 2 — real geometry

Replace fake dimensions with LiDAR-derived oriented bounding boxes.

### Phase 3 — AR

Use solver transforms to place ghost objects into the real container.

### Phase 4 — polish

Only after the full loop works:
- prettier UI
- mesh-aware packing
- automatic placement verification
- scan confidence
- improved animation

## Definition of Done

The MVP succeeds if the demo can:

1. scan several objects
2. scan a container
3. show a reconstructed 3D scene
4. generate a valid packing arrangement
5. animate the result
6. enter AR
7. show where at least one physical object should be placed
