import inspect
import json
import math
import subprocess
import sys
import unittest
from pathlib import Path

import numpy as np

from physics.geometry import obb_from, obb_vertices
from physics.io import (
    apply_placements,
    object_from_box_fit,
    object_from_scanned_item,
    result_to_json,
    scene_from_dict,
    scene_to_dict,
    validate,
)
from physics.schema import Object
from physics.validator import validate_layout
from tests import fixtures as fx

REPO_ROOT = Path(__file__).resolve().parent.parent


def _fixture_funcs():
    """Every zero-arg Scene factory defined in tests/fixtures.py."""
    return [
        obj
        for name, obj in sorted(vars(fx).items())
        if inspect.isfunction(obj)
        and obj.__module__ == fx.__name__
        and not name.startswith("_")
    ]


class TestSceneRoundTrip(unittest.TestCase):
    def test_round_trip_every_fixture(self):
        for factory in _fixture_funcs():
            with self.subTest(fixture=factory.__name__):
                scene = factory()
                rebuilt = scene_from_dict(scene_to_dict(scene))
                self.assertEqual(rebuilt, scene)

    def test_missing_optional_fields_use_schema_defaults(self):
        d = {
            "container": {"id": "c", "dimensions": [1.0, 1.0, 1.0]},
            "objects": [{"id": "o", "dimensions": [0.1, 0.1, 0.1], "position": [0, 0, 0]}],
        }
        scene = scene_from_dict(d)
        self.assertEqual(scene.container.position, (0.0, 0.0, 0.0))
        self.assertEqual(scene.container.rotation, (0.0, 0.0, 0.0, 1.0))
        obj = scene.objects[0]
        self.assertEqual(obj.rotation, (0.0, 0.0, 0.0, 1.0))
        self.assertEqual(obj.mass_kg, 1.0)
        self.assertEqual(obj.rigidity, "rigid")
        self.assertEqual(obj.compressibility_k, 1.0)
        self.assertFalse(obj.constraints.fragile)

    def test_null_rotation_is_identity(self):
        d = {
            "container": {"id": "c", "dimensions": [1.0, 1.0, 1.0], "rotation": None},
            "objects": [
                {"id": "o", "dimensions": [0.1, 0.1, 0.1], "position": [0, 0, 0], "rotation": None}
            ],
        }
        scene = scene_from_dict(d)
        self.assertEqual(scene.container.rotation, (0.0, 0.0, 0.0, 1.0))
        self.assertEqual(scene.objects[0].rotation, (0.0, 0.0, 0.0, 1.0))


class TestScannedItem(unittest.TestCase):
    def test_cm_to_m_mapping(self):
        item = {"id": "shoe-1", "width": 29.0, "depth": 12.0, "height": 11.0}
        obj = object_from_scanned_item(item)
        self.assertAlmostEqual(obj.dimensions[0], 0.29, places=9)
        self.assertAlmostEqual(obj.dimensions[1], 0.11, places=9)
        self.assertAlmostEqual(obj.dimensions[2], 0.12, places=9)
        self.assertEqual(obj.id, "shoe-1")
        self.assertEqual(obj.position, (0.0, 0.0, 0.0))
        self.assertEqual(obj.rotation, (0.0, 0.0, 0.0, 1.0))

    def test_id_and_pose_overrides(self):
        item = {"id": "ignored", "width": 10.0, "depth": 10.0, "height": 10.0}
        obj = object_from_scanned_item(
            item, id="custom", position=(1.0, 2.0, 3.0), rotation=(0.0, 0.0, 0.0, 1.0), mass_kg=5.0
        )
        self.assertEqual(obj.id, "custom")
        self.assertEqual(obj.position, (1.0, 2.0, 3.0))
        self.assertEqual(obj.mass_kg, 5.0)


class TestBoxFitOrientation(unittest.TestCase):
    """Numerically verify the BoxFit -> quaternion formula rather than trust it:
    build the Object, take its actual OBB vertices, and check the width-edge
    vector (corners differing only in local-x sign, SIGNS rows 0 and 4) is
    parallel to `axis` and has length `width_m`, and that the top face sits
    at center.y + height/2.
    """

    def _check(self, deg: float):
        theta = math.radians(deg)
        axis = (math.cos(theta), 0.0, -math.sin(theta))
        width_m, depth_m, height_m = 0.29, 0.12, 0.11
        center = (0.4, 0.3, -0.2)
        obj = object_from_box_fit("shoe", width_m, depth_m, height_m, center, axis)
        verts = obb_vertices(obb_from(obj))
        v0, v4 = verts[0], verts[4]
        edge = v4 - v0
        length = float(np.linalg.norm(edge))
        cos_angle = float(np.dot(edge, axis)) / (length * np.linalg.norm(axis))
        self.assertGreater(abs(cos_angle), 1 - 1e-9, msg=f"deg={deg}")
        self.assertAlmostEqual(length, width_m, places=9, msg=f"deg={deg}")
        top_y = float(verts[:, 1].max())
        self.assertAlmostEqual(top_y, center[1] + height_m / 2.0, places=9, msg=f"deg={deg}")

    def test_0_deg(self):
        self._check(0.0)

    def test_35_deg(self):
        self._check(35.0)

    def test_90_deg(self):
        self._check(90.0)

    def test_neg_120_deg(self):
        self._check(-120.0)


class TestApplyPlacements(unittest.TestCase):
    def test_position_rotation_spelling(self):
        scene = fx.valid_packed_scene()
        new_scene = apply_placements(
            scene, [{"id": "shoe", "position": [1.0, 2.0, 3.0], "rotation": [0.0, 1.0, 0.0, 0.0]}]
        )
        shoe = next(o for o in new_scene.objects if o.id == "shoe")
        self.assertEqual(shoe.position, (1.0, 2.0, 3.0))
        self.assertEqual(shoe.rotation, (0.0, 1.0, 0.0, 0.0))

    def test_target_position_rotation_spelling(self):
        scene = fx.valid_packed_scene()
        new_scene = apply_placements(
            scene,
            [{"id": "shoe", "target_position": [4.0, 5.0, 6.0], "target_rotation": [1.0, 0.0, 0.0, 0.0]}],
        )
        shoe = next(o for o in new_scene.objects if o.id == "shoe")
        self.assertEqual(shoe.position, (4.0, 5.0, 6.0))
        self.assertEqual(shoe.rotation, (1.0, 0.0, 0.0, 0.0))

    def test_unmentioned_objects_keep_pose(self):
        scene = fx.valid_packed_scene()
        original_laptop = next(o for o in scene.objects if o.id == "laptop")
        new_scene = apply_placements(scene, [{"id": "shoe", "position": [0.0, 0.0, 0.0]}])
        new_laptop = next(o for o in new_scene.objects if o.id == "laptop")
        self.assertEqual(new_laptop, original_laptop)

    def test_unknown_id_raises(self):
        scene = fx.valid_packed_scene()
        with self.assertRaises(ValueError) as ctx:
            apply_placements(scene, [{"id": "nonexistent", "position": [0.0, 0.0, 0.0]}])
        self.assertIn("nonexistent", str(ctx.exception))

    def test_does_not_mutate_input(self):
        scene = fx.valid_packed_scene()
        original_positions = [o.position for o in scene.objects]
        apply_placements(scene, [{"id": "shoe", "position": [9.0, 9.0, 9.0]}])
        self.assertEqual([o.position for o in scene.objects], original_positions)


class TestValidate(unittest.TestCase):
    def test_reproduces_collision_fixture(self):
        base = fx.valid_packed_scene()
        shoe = next(o for o in fx.scene_with_collision().objects if o.id == "shoe")
        placements = [{"id": "shoe", "position": list(shoe.position)}]
        got = validate(base, placements)
        expected = validate_layout(fx.scene_with_collision())
        self.assertEqual(got, expected)

    def test_no_placements_matches_validate_layout(self):
        scene = fx.valid_packed_scene()
        self.assertEqual(validate(scene), validate_layout(scene))

    def test_unknown_placement_id_never_raises(self):
        scene = fx.valid_packed_scene()
        result = validate(scene, [{"id": "nonexistent", "position": [0.0, 0.0, 0.0]}])
        self.assertFalse(result["valid"])
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(len(result["violations"]), 1)
        v = result["violations"][0]
        self.assertEqual(v["type"], "MALFORMED_GEOMETRY")
        self.assertEqual(v["object"], "nonexistent")
        self.assertEqual(result["warnings"], [])


class TestResultToJson(unittest.TestCase):
    def test_every_fixture_round_trips_through_json(self):
        for factory in _fixture_funcs():
            with self.subTest(fixture=factory.__name__):
                result = validate_layout(factory())
                text = result_to_json(result)
                parsed = json.loads(text)
                self.assertEqual(parsed, result)


class TestCli(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "physics", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_validate_valid_scene_exit_0(self):
        proc = self._run("validate", "examples/scene_carry_on.json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        parsed = json.loads(proc.stdout)
        self.assertTrue(parsed["valid"])

    def test_validate_collision_placements_exit_1(self):
        proc = self._run(
            "validate",
            "examples/scene_carry_on.json",
            "--placements",
            "examples/placements_collision.json",
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        parsed = json.loads(proc.stdout)
        self.assertFalse(parsed["valid"])

    def test_validate_missing_file_exit_2(self):
        proc = self._run("validate", "examples/does_not_exist.json")
        self.assertEqual(proc.returncode, 2)

    def test_example_prints_json(self):
        proc = self._run("example")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        parsed = json.loads(proc.stdout)
        self.assertIn("container", parsed)
        self.assertIn("objects", parsed)

    def test_scan_to_object_prints_json(self):
        proc = self._run("scan-to-object", "examples/scanned_item.json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        parsed = json.loads(proc.stdout)
        self.assertAlmostEqual(parsed["dimensions"][0], 0.29, places=9)
        self.assertAlmostEqual(parsed["dimensions"][1], 0.11, places=9)
        self.assertAlmostEqual(parsed["dimensions"][2], 0.12, places=9)


if __name__ == "__main__":
    unittest.main()
