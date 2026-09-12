"""Bit-identical output guard for `validate_layout`.

Locks the JSON of `validate_layout` on a fixed scene corpus (every fixture in
`tests.fixtures`, the benchmark grids, and 30 seeded random scenes with fully
random orientations) against snapshots recorded in
`tests/snapshots/validator_v2.json`. Optimization passes on the physics modules
must not move a single bit of output; if this test fails, the change altered
results, not just speed.

Regenerate ONLY when an intended semantic change is being made:
    PYTHONPATH=. python3 tests/test_snapshot_regression.py --write
"""
from __future__ import annotations

import json
import math
import random
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from physics.geometry import SIGNS, obb_from, obb_vertices
from physics.schema import Constraints, Container, Object, Scene
from physics.validator import validate_layout
from tests.benchmark_validator import build_scene
from tests import fixtures

SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "validator_v2.json"

_LOCKS = (None, "this_side_up", "flat_only", "horizontal")


def _fixture_scenes() -> dict[str, Scene]:
    """Every zero-argument Scene factory defined in tests.fixtures."""
    out: dict[str, Scene] = {}
    for name in sorted(dir(fixtures)):
        fn = getattr(fixtures, name)
        if name.startswith("_") or not callable(fn):
            continue
        if getattr(fn, "__module__", None) != fixtures.__name__:
            continue
        if fn.__code__.co_argcount:
            continue
        scene = fn()
        if isinstance(scene, Scene):
            out[f"fixture:{name}"] = scene
    return out


def _grid_scenes() -> dict[str, Scene]:
    return {
        f"grid:n{n}:cell{cell}": build_scene(n, cell=cell)
        for n in (5, 20, 40)
        for cell in (0.30, 0.20, 0.19)
    }


def _rand_quat(rng: random.Random) -> tuple[float, float, float, float]:
    """Uniform random unit quaternion (Shoemake) -- yaw, pitch and roll."""
    u1, u2, u3 = rng.random(), rng.random(), rng.random()
    r1, r2 = math.sqrt(1.0 - u1), math.sqrt(u1)
    t1, t2 = 2.0 * math.pi * u2, 2.0 * math.pi * u3
    return (r1 * math.sin(t1), r1 * math.cos(t1), r2 * math.sin(t2), r2 * math.cos(t2))


def _random_scene(seed: int) -> Scene:
    """3-8 randomly oriented boxes in a 3 m cube; every other one settled flush
    on the floor (so support sees real contact polygons, not just floaters)."""
    rng = random.Random(seed)
    container = Container(id="box3m", dimensions=(3.0, 3.0, 3.0), position=(0.0, 0.0, 0.0))
    floor_y = -1.5
    objects: list[Object] = []
    for k in range(3 + seed % 6):
        obj = Object(
            id=f"r{seed:02d}_{k}",
            dimensions=(rng.uniform(0.02, 0.5), rng.uniform(0.02, 0.5), rng.uniform(0.02, 0.5)),
            position=(rng.uniform(-0.6, 0.6), 0.0, rng.uniform(-0.6, 0.6)),
            rotation=_rand_quat(rng),
            mass_kg=rng.uniform(0.05, 5.0),
            constraints=Constraints(
                fragile=rng.random() < 0.3,
                keep_upright=rng.random() < 0.3,
                cannot_support_weight=rng.random() < 0.2,
                heavy=rng.random() < 0.2,
                orientation_lock=_LOCKS[rng.randrange(len(_LOCKS))],
            ),
        )
        x, _, z = obj.position
        if k % 2 == 0:  # settle flush on the container floor
            y = floor_y - float(obb_vertices(obb_from(obj))[:, 1].min())
        else:
            y = rng.uniform(-0.6, 0.6)
        objects.append(replace(obj, position=(x, y, z)))
    return Scene(container=container, objects=objects)


def all_scenes() -> dict[str, Scene]:
    scenes = _fixture_scenes()
    scenes.update(_grid_scenes())
    scenes.update({f"random:{seed:02d}": _random_scene(seed) for seed in range(30)})
    return scenes


def _dump(result: dict) -> str:
    return json.dumps(result, sort_keys=True, default=float)


class SnapshotRegressionTest(unittest.TestCase):
    def test_validate_layout_output_unchanged(self):
        expected = json.loads(SNAPSHOT_PATH.read_text())
        scenes = all_scenes()
        self.assertEqual(sorted(expected), sorted(scenes), "scene corpus changed")
        for name, scene in scenes.items():
            with self.subTest(scene=name):
                self.assertEqual(expected[name], _dump(validate_layout(scene)))


class FaceCycleTest(unittest.TestCase):
    """The support.py face fast path must reproduce `_hull` exactly."""

    def test_cycles_are_box_faces_in_cyclic_order(self):
        from physics.support import _FACE_CYCLES

        self.assertEqual(len(_FACE_CYCLES), 6)
        for bits, cycle in _FACE_CYCLES.items():
            self.assertEqual(bits, sum(1 << i for i in cycle))
            # all four corners share one local axis sign -> they are a face
            shared = [
                a for a in range(3) if len({SIGNS[i, a] for i in cycle}) == 1
            ]
            self.assertEqual(len(shared), 1)
            # consecutive corners differ in exactly one of the other two signs
            for i in range(4):
                p, q = cycle[i], cycle[(i + 1) % 4]
                self.assertEqual(sum(SIGNS[p, a] != SIGNS[q, a] for a in range(3)), 1)

    def test_fast_path_matches_hull_on_random_boxes(self):
        from physics.support import _FACE_CYCLES, _face_hull, _hull

        rng = random.Random(1234)
        hits = 0
        for _ in range(3000):
            obj = Object(
                id="o",
                dimensions=(rng.uniform(0.02, 0.5), rng.uniform(0.02, 0.5), rng.uniform(0.02, 0.5)),
                position=(0.0, 0.0, 0.0),
                rotation=_rand_quat(rng) if rng.random() < 0.7 else (0.0, 0.0, 0.0, 1.0),
            )
            verts = obb_vertices(obb_from(obj))
            row = [(float(p[0]), float(p[2])) for p in verts]
            ys = verts[:, 1]
            for eps in (1e-3, 1e-2, 0.1):
                low = ys <= ys.min() + eps
                bits = sum(1 << i for i in range(8) if low[i])
                pts = [row[i] for i in range(8) if low[i]]
                fast = _face_hull(row, _FACE_CYCLES.get(bits))
                if fast is None:
                    continue
                hits += 1
                self.assertEqual(fast, _hull(pts))
        self.assertGreater(hits, 100)  # the fast path actually fired


def _write() -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    snapshots = {name: _dump(validate_layout(scene)) for name, scene in all_scenes().items()}
    SNAPSHOT_PATH.write_text(json.dumps(snapshots, indent=0, sort_keys=True) + "\n")
    print(f"wrote {len(snapshots)} snapshots, {SNAPSHOT_PATH.stat().st_size / 1024:.1f} KiB")


if __name__ == "__main__":
    if "--write" in sys.argv:
        _write()
    else:
        print(__doc__)
