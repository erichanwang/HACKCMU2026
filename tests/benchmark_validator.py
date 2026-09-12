"""Plain timing script for `validate_layout` at 5/10/20 objects. Not unittest.

Builds a simple non-colliding grid of objects inside a large container and
times `validate_layout` with `time.perf_counter`. Run: python3 tests/benchmark_validator.py
"""
import math
import time

from physics.schema import Container, Object, Scene
from physics.validator import validate_layout


def build_scene(n: int) -> Scene:
    # Grid cells 0.3m apart, box 0.2m -> no collisions. Container sized to fit.
    cols = math.ceil(math.sqrt(n))
    cell = 0.3
    box = 0.2
    container_size = cols * cell + 0.5
    container = Container(id="suitcase", dimensions=(container_size, 1.0, container_size))
    floor_y = -0.5 + box / 2.0
    objects = []
    for i in range(n):
        row, col = divmod(i, cols)
        x = -container_size / 2 + cell / 2 + col * cell
        z = -container_size / 2 + cell / 2 + row * cell
        objects.append(Object(id=f"obj{i}", dimensions=(box, box, box), position=(x, floor_y, z)))
    return Scene(container=container, objects=objects)


if __name__ == "__main__":
    for n in (5, 10, 20):
        scene = build_scene(n)
        start = time.perf_counter()
        result = validate_layout(scene)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert result["valid"], result["violations"]
        print(f"{n} objects: {elapsed_ms:.1f}ms")
