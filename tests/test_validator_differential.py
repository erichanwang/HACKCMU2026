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


def _random_scene(rng: random.Random) -> Scene:
    """A container plus 1-4 boxes with random size/position/rotation -- some
    fit cleanly, some collide with each other or penetrate the walls.
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
        objects.append(
            Object(id=f"obj{i}", dimensions=dims, position=position, rotation=rotation)
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
        # NOTE: `_random_scene` only ever builds plain box `Object`s (no
        # `footprint`), so every scene here is box-only by construction. Do
        # NOT add footprint/hull objects to this generator -- the Swift CLI
        # has no hull support (see `test_swift_ignores_hull_and_uses_bbox`
        # below) and would silently disagree, defeating this test's purpose.
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

    def test_swift_ignores_hull_and_uses_bbox(self):
        """Documents the EXPECTED divergence on hull/footprint objects.

        `physics.schema.Object.footprint` (added on top of `loop`) lets an
        object be a convex prism instead of a box; `physics.validator` is
        hull-aware. The Swift `SceneObject` decoder
        (`swift/PackPhysics/Sources/PackPhysics/Schema.swift`) has no
        `footprint` case in its `CodingKeys`/`init(from:)`, so it silently
        ignores that JSON field and treats the object as a plain box using
        `dimensions` (the footprint's bounding box, per `schema.py`'s
        docstring). This scene is built so a second object sits inside
        `hull_obj`'s bounding box but outside its actual (chamfered) hull:
        Python sees no collision, Swift -- having silently boxed the hull
        object -- does. If Swift ever gains real hull support, or Python's
        hull handling changes, this assertion should break loudly rather
        than have someone mistake it for the box-only test above.
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
        self.assertFalse(sw_result["valid"], "swift (box-only) should see hull_obj's bbox collide")
        self.assertEqual(_violation_types(sw_result), {"OBJECT_COLLISION"})


if __name__ == "__main__":
    unittest.main()
