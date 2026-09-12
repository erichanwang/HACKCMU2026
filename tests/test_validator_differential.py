"""Differential fuzz test: Python's `physics.validator.validate_layout` vs.
the Swift `packphysics validate` CLI, on the same randomly generated scenes.

Reuses the existing scene schema (`physics.schema.Scene`) and JSON wire
format (`physics.io.scene_to_dict`, the same shape `physics/__main__.py` and
the Swift CLI's `sceneFromJSON` already agree on -- see tests/test_io.py and
swift/PackPhysics/Sources/PackPhysicsCLI/main.swift).

Requires the release CLI to be built first:
    cd swift/PackPhysics && source swiftenv.sh && swift build -c release
If it isn't built, every test here is skipped (not failed) -- building takes
minutes, too slow to do inline in a test run.
"""
from __future__ import annotations

import json
import math
import random
import subprocess
import unittest
from pathlib import Path

from physics.io import scene_to_dict
from physics.schema import Container, Object, Scene
from physics.validator import validate_layout

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_BIN = REPO_ROOT / "swift" / "PackPhysics" / ".build" / "release" / "packphysics"

N_SCENES = 60
SEED = 20260912


def _random_unit_quaternion(rng: random.Random) -> tuple[float, float, float, float]:
    v = [rng.gauss(0.0, 1.0) for _ in range(4)]
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / n for c in v)


def _random_convex_footprint(rng: random.Random, hx: float, hz: float) -> list[tuple[float, float]]:
    """A random convex polygon inscribed in the +-hx x +-hz rectangle: points on
    an ellipse at random angles, hulled -- always >= 3 points, always inside
    the box (the invariant `physics.geometry.footprint_local` enforces)."""
    n = rng.randint(4, 7)
    pts = []
    for _ in range(n):
        theta = rng.uniform(0.0, 2.0 * math.pi)
        r = rng.uniform(0.5, 1.0)
        pts.append((hx * r * math.cos(theta), hz * r * math.sin(theta)))
    return pts


def _random_scene(rng: random.Random) -> Scene:
    """A container plus 1-4 boxes (occasionally convex-hull prisms) with random
    size/position/rotation -- some fit cleanly, some collide with each other or
    penetrate the walls.
    """
    container = Container(id="box", dimensions=(1.2, 1.2, 1.2), position=(0.0, 0.6, 0.0))
    objects = []
    for i in range(rng.randint(1, 4)):
        dims = tuple(rng.uniform(0.05, 0.5) for _ in range(3))
        # Range wide enough to sometimes fit inside the container (half-extent
        # 0.6) and sometimes stick through a wall or overlap another box.
        position = tuple(rng.uniform(-0.9, 0.9) for _ in range(2))
        position = (position[0], rng.uniform(0.0, 1.2), position[1])
        rotation = _random_unit_quaternion(rng) if rng.random() < 0.5 else (0.0, 0.0, 0.0, 1.0)
        footprint = (
            _random_convex_footprint(rng, dims[0] / 2.0, dims[2] / 2.0)
            if rng.random() < 0.3
            else None
        )
        objects.append(
            Object(
                id=f"obj{i}", dimensions=dims, position=position, rotation=rotation,
                footprint=footprint,
            )
        )
    return Scene(container=container, objects=objects)


def _run_swift_validate(scene: Scene, tmp_path: Path) -> dict:
    scene_path = tmp_path / "scene.json"
    scene_path.write_text(json.dumps(scene_to_dict(scene)))
    proc = subprocess.run(
        [str(CLI_BIN), "validate", str(scene_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode in (0, 1), f"swift CLI errored: {proc.stderr}"
    return json.loads(proc.stdout)


def _violation_types(result: dict) -> set:
    return {v["type"] for v in result["violations"]}


@unittest.skipUnless(CLI_BIN.exists(), f"packphysics CLI not built at {CLI_BIN}; see module docstring")
class TestValidatorDifferential(unittest.TestCase):
    def test_python_and_swift_agree_on_random_scenes(self):
        # `_random_scene` occasionally gives an object a convex-hull footprint
        # (see `_random_convex_footprint`) -- the Swift CLI now has prism
        # support (see `test_swift_agrees_on_hull_scene` below), so this
        # exercises that narrow phase too, not just the box-only path.
        import tempfile

        rng = random.Random(SEED)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for i in range(N_SCENES):
                scene = _random_scene(rng)
                py_result = validate_layout(scene)
                sw_result = _run_swift_validate(scene, tmp_path)
                self.assertEqual(
                    py_result["valid"], sw_result["valid"],
                    f"scene {i}: valid mismatch -- python={py_result['valid']} "
                    f"swift={sw_result['valid']}\nscene={json.dumps(scene_to_dict(scene))}",
                )
                self.assertEqual(
                    _violation_types(py_result), _violation_types(sw_result),
                    f"scene {i}: violation types mismatch -- python={_violation_types(py_result)} "
                    f"swift={_violation_types(sw_result)}\nscene={json.dumps(scene_to_dict(scene))}",
                )

    def test_swift_agrees_on_hull_scene(self):
        """Swift now has prism/footprint support (ported from the Python
        reference: `swift/PackPhysics/Sources/PackPhysics/Schema.swift`'s
        `SceneObject` decodes `footprint`, and `Geometry.swift`/`Collision.swift`/
        `Support.swift`/`Containment.swift`/`Metrics.swift` carry it through
        collision, support, containment and scene metrics), so it agrees with
        Python on a hull scene instead of silently boxing it.

        This scene is built so a second object sits inside `hull_obj`'s
        bounding box but outside its actual (chamfered) hull: a box-only
        implementation would report a collision; a hull-aware one sees none.
        This used to document the EXPECTED divergence (see git history); now
        it pins the agreement the port exists to deliver.
        """
        import tempfile

        container = Container(id="box", dimensions=(1.2, 1.2, 1.2), position=(0.0, 0.6, 0.0))
        # Convex pentagon: the square footprint with one corner chamfered off.
        hull = [(-0.3, -0.3), (0.3, -0.3), (0.3, 0.1), (0.1, 0.3), (-0.3, 0.3)]
        hull_obj = Object(
            id="hull_obj", dimensions=(0.6, 0.4, 0.6), position=(0.0, 0.2, 0.0),
            rotation=(0.0, 0.0, 0.0, 1.0), footprint=hull,
        )
        # Sits in the chamfered-off corner: inside hull_obj's bbox, outside its hull.
        box_obj = Object(
            id="box_obj", dimensions=(0.08, 0.4, 0.08), position=(0.27, 0.2, 0.27),
            rotation=(0.0, 0.0, 0.0, 1.0),
        )
        scene = Scene(container=container, objects=[hull_obj, box_obj])

        py_result = validate_layout(scene)
        self.assertTrue(py_result["valid"], "python (hull-aware) should see no collision")

        with tempfile.TemporaryDirectory() as tmp:
            sw_result = _run_swift_validate(scene, Path(tmp))
        self.assertTrue(sw_result["valid"], "swift (now hull-aware) should also see no collision")
        self.assertEqual(_violation_types(sw_result), set())


if __name__ == "__main__":
    unittest.main()
