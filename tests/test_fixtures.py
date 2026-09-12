"""Sanity checks for tests/fixtures.py -- catches fixture-authoring bugs
(duplicate ids, non-finite numbers, bad quaternions) without duplicating the
real collision/containment/support logic those fixtures exist to feed.
"""
import math
import unittest

from physics.schema import Scene
from tests import fixtures

FIXTURE_FUNCS = [
    fixtures.valid_packed_scene,
    fixtures.scene_with_collision,
    fixtures.scene_with_wall_penetration,
    fixtures.scene_with_floating_object,
    fixtures.scene_with_precarious_balance,
    fixtures.scene_nearly_full,
    fixtures.scene_object_touching_wall,
    fixtures.scene_rotated_object_in_corner,
    fixtures.scene_stacked_objects,
    fixtures.scene_oversized_object,
    fixtures.scene_with_soft_item_compression,
]


def _is_finite_vec3(v) -> bool:
    return len(v) == 3 and all(isinstance(c, (int, float)) and math.isfinite(c) for c in v)


class TestFixtureSanity(unittest.TestCase):
    def test_all_fixtures_return_scene_with_container_and_objects(self):
        for fn in FIXTURE_FUNCS:
            with self.subTest(fixture=fn.__name__):
                scene = fn()
                self.assertIsInstance(scene, Scene)
                self.assertIsNotNone(scene.container)
                self.assertTrue(len(scene.objects) > 0, "fixture has no objects")

    def test_object_ids_unique_within_each_fixture(self):
        for fn in FIXTURE_FUNCS:
            with self.subTest(fixture=fn.__name__):
                scene = fn()
                ids = [o.id for o in scene.objects]
                self.assertEqual(len(ids), len(set(ids)), f"duplicate ids in {ids}")

    def test_dimensions_finite_and_positive(self):
        for fn in FIXTURE_FUNCS:
            scene = fn()
            entities = [scene.container] + list(scene.objects)
            for e in entities:
                with self.subTest(fixture=fn.__name__, id=getattr(e, "id", "container")):
                    self.assertTrue(_is_finite_vec3(e.dimensions))
                    self.assertTrue(all(d > 0 for d in e.dimensions))

    def test_positions_finite(self):
        for fn in FIXTURE_FUNCS:
            scene = fn()
            entities = [scene.container] + list(scene.objects)
            for e in entities:
                with self.subTest(fixture=fn.__name__, id=getattr(e, "id", "container")):
                    self.assertTrue(_is_finite_vec3(e.position))

    def test_quaternions_are_unit_length(self):
        for fn in FIXTURE_FUNCS:
            scene = fn()
            entities = [scene.container] + list(scene.objects)
            for e in entities:
                with self.subTest(fixture=fn.__name__, id=getattr(e, "id", "container")):
                    x, y, z, w = e.rotation
                    norm = math.sqrt(x * x + y * y + z * z + w * w)
                    self.assertAlmostEqual(norm, 1.0, places=6)

    def test_mass_positive_for_objects(self):
        for fn in FIXTURE_FUNCS:
            scene = fn()
            for o in scene.objects:
                with self.subTest(fixture=fn.__name__, id=o.id):
                    self.assertGreater(o.mass_kg, 0.0)


    def test_soft_item_compression_is_warning_not_violation(self):
        from physics.validator import validate_layout

        result = validate_layout(fixtures.scene_with_soft_item_compression())
        self.assertTrue(result["valid"])
        self.assertEqual(result["violations"], [])
        self.assertEqual(len(result["warnings"]), 1)
        self.assertEqual(result["warnings"][0]["type"], "SOFT_COMPRESSION")


if __name__ == "__main__":
    unittest.main()
