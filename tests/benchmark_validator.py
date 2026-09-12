"""Timing script for `validate_layout`. Not unittest.

Three scene families, because a sparse grid never exercises the narrow phase:
- sparse : 0.20 m boxes on a 0.30 m grid -> no AABB overlaps (broad phase only)
- touching: boxes exactly flush with their neighbours -> every neighbour pair
            survives the broad phase and must be separated by SAT (touching is
            NOT colliding), so this is the honest "packed suitcase" cost
- dense  : boxes on a 0.19 m grid -> most neighbour pairs truly collide

Run: PYTHONPATH=. python3 tests/benchmark_validator.py
"""
import math
import time

from physics.schema import Container, Object, Scene
from physics.validator import validate_layout


def build_scene(n: int, cell: float = 0.30, box: float = 0.20) -> Scene:
    cols = math.ceil(math.sqrt(n))
    size = cols * cell + 0.5
    container = Container(id="suitcase", dimensions=(size, 1.0, size))
    floor_y = -0.5 + box / 2.0
    objects = []
    for i in range(n):
        row, col = divmod(i, cols)
        x = -size / 2 + cell / 2 + col * cell
        z = -size / 2 + cell / 2 + row * cell
        objects.append(Object(id=f"obj{i:02d}", dimensions=(box, box, box), position=(x, floor_y, z)))
    return Scene(container=container, objects=objects)


def time_call(scene: Scene, reps: int = 30) -> tuple[float, dict]:
    result = validate_layout(scene)  # warm
    t0 = time.perf_counter()
    for _ in range(reps):
        result = validate_layout(scene)
    return (time.perf_counter() - t0) / reps * 1e3, result


if __name__ == "__main__":
    families = {
        "sparse (no AABB overlap)": dict(cell=0.30),
        "touching (flush, SAT separates)": dict(cell=0.20),
        "dense (overlapping)": dict(cell=0.19),
    }
    print(f"{'scene':34s} {'n':>3s} {'ms/call':>8s}  collisions")
    for name, kw in families.items():
        for n in (5, 10, 20, 40):
            ms, res = time_call(build_scene(n, **kw))
            ncol = sum(1 for v in res["violations"] if v["type"] == "OBJECT_COLLISION")
            print(f"{name:34s} {n:3d} {ms:8.2f}  {ncol}")
