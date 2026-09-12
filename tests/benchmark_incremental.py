"""Plain timing script for PlacementValidator.try_place vs. full validate_layout.

Builds a dense 20-object grid (0.20m boxes on a 0.21m grid -> no collisions
among the 20), commits all 20, then times 1000 try_place calls of a 21st
candidate box positioned to overlap a few neighbours (worst case: collision
narrow-phase actually runs, not just the AABB prefilter bailing early).
Run: PYTHONPATH=. python3 tests/benchmark_incremental.py
"""
import time

from physics.incremental import PlacementValidator
from physics.schema import Container, Object, Scene
from physics.validator import validate_layout

GRID_CELL = 0.21
BOX = 0.20
N = 20


def build_grid():
    cols = 5  # 20 objects -> 5x4 grid
    container_size = cols * GRID_CELL + 1.0
    container = Container(id="bin", dimensions=(container_size, 1.0, container_size))
    floor_y = -0.5 + BOX / 2.0
    objects = []
    for i in range(N):
        row, col = divmod(i, cols)
        x = -container_size / 2 + GRID_CELL / 2 + col * GRID_CELL
        z = -container_size / 2 + GRID_CELL / 2 + row * GRID_CELL
        objects.append(Object(id=f"obj{i}", dimensions=(BOX, BOX, BOX), position=(x, floor_y, z)))
    return container, objects


if __name__ == "__main__":
    container, objects = build_grid()
    pv = PlacementValidator(container)
    for obj in objects:
        r = pv.place(obj)
        assert r["valid"], (obj.id, r["violations"])

    # Candidate overlapping 2-4 neighbours near the grid's middle.
    floor_y = -0.5 + BOX / 2.0
    candidate = Object(id="candidate", dimensions=(BOX, BOX, BOX), position=(0.0, floor_y, 0.0))

    iters = 1000
    start = time.perf_counter()
    for _ in range(iters):
        pv.try_place(candidate)
    elapsed_us_per_call = (time.perf_counter() - start) * 1e6 / iters
    print(f"try_place: {elapsed_us_per_call:.2f} us/call over {iters} calls "
          f"({N} committed objects, {N + 1}-object scene)")

    full_scene = Scene(container=container, objects=objects + [candidate])
    iters_full = 200
    start = time.perf_counter()
    for _ in range(iters_full):
        validate_layout(full_scene)
    elapsed_us_full = (time.perf_counter() - start) * 1e6 / iters_full
    print(f"validate_layout (full {N + 1}-object scene): {elapsed_us_full:.2f} us/call")
    print(f"speedup: {elapsed_us_full / elapsed_us_per_call:.1f}x")
