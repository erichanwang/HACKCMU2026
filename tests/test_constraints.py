import math
import unittest

from physics.constraints import check_constraints
from physics.schema import Constraints, Container, Object, Scene


def quat_x_rot(deg: float) -> tuple[float, float, float, float]:
    """Quaternion for a rotation of `deg` about world X (tips local Y over)."""
    h = math.radians(deg) / 2.0
    return (math.sin(h), 0.0, 0.0, math.cos(h))


def make_scene(objects: list[Object]) -> Scene:
    container = Container(id="c", dimensions=(1.0, 1.0, 1.0), position=(0.0, 0.0, 0.0))
    return Scene(container=container, objects=objects)


class TestUprightAndOrientation(unittest.TestCase):
    def test_upright_bottle_no_violation(self):
        bottle = Object(
            id="bottle",
            dimensions=(0.1, 0.3, 0.1),
            position=(0.0, 0.15, 0.0),
            constraints=Constraints(keep_upright=True),
        )
        violations, warnings = check_constraints(make_scene([bottle]))
        self.assertEqual(violations, [])
        self.assertEqual(warnings, [])

    def test_tilted_bottle_violation(self):
        bottle = Object(
            id="bottle",
            dimensions=(0.1, 0.3, 0.1),
            position=(0.0, 0.05, 0.0),
            rotation=quat_x_rot(90.0),
            constraints=Constraints(keep_upright=True),
        )
        violations, _ = check_constraints(make_scene([bottle]))
        self.assertEqual(len(violations), 1)
        v = violations[0]
        self.assertEqual(v.type, "LIQUID_NOT_UPRIGHT")
        self.assertAlmostEqual(v.details["tilt_deg"], 90.0, delta=0.5)

    def test_flat_only_lying_down_ok(self):
        box = Object(
            id="book",
            dimensions=(0.2, 0.05, 0.3),
            position=(0.0, 0.025, 0.0),
            constraints=Constraints(orientation_lock="flat_only"),
        )
        violations, _ = check_constraints(make_scene([box]))
        self.assertEqual(violations, [])

    def test_flat_only_standing_on_edge_violation(self):
        box = Object(
            id="book",
            dimensions=(0.2, 0.05, 0.3),
            position=(0.0, 0.1, 0.0),
            rotation=quat_x_rot(90.0),
            constraints=Constraints(orientation_lock="flat_only"),
        )
        violations, _ = check_constraints(make_scene([box]))
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].type, "INVALID_ORIENTATION")
        self.assertEqual(violations[0].details["lock"], "flat_only")


class TestCannotSupportWeight(unittest.TestCase):
    def test_laptop_alone_no_violation(self):
        laptop = Object(
            id="laptop",
            dimensions=(0.3, 0.02, 0.2),
            position=(0.0, 0.01, 0.0),
            constraints=Constraints(cannot_support_weight=True),
        )
        violations, _ = check_constraints(make_scene([laptop]))
        self.assertEqual(violations, [])

    def test_shoe_on_laptop_violation(self):
        laptop = Object(
            id="laptop",
            dimensions=(0.3, 0.02, 0.2),
            position=(0.0, 0.01, 0.0),
            mass_kg=1.5,
            constraints=Constraints(cannot_support_weight=True),
        )
        shoe = Object(
            id="shoe",
            dimensions=(0.1, 0.1, 0.25),
            position=(0.0, 0.02 + 0.05, 0.0),  # sits right on top of laptop's top face
            mass_kg=0.4,
        )
        violations, _ = check_constraints(make_scene([laptop, shoe]))
        self.assertEqual(len(violations), 1)
        v = violations[0]
        self.assertEqual(v.type, "FRAGILE_OBJECT_OVERLOADED")
        self.assertEqual(v.object_id, "laptop")
        self.assertAlmostEqual(v.details["supported_weight_kg"], 0.4)


class TestFragileWarning(unittest.TestCase):
    def test_fragile_with_load_is_warning_not_violation(self):
        glass = Object(
            id="glass",
            dimensions=(0.1, 0.1, 0.1),
            position=(0.0, 0.05, 0.0),
            mass_kg=0.3,
            constraints=Constraints(fragile=True),
        )
        shirt = Object(
            id="shirt",
            dimensions=(0.2, 0.02, 0.2),
            position=(0.0, 0.1 + 0.01, 0.0),
            mass_kg=0.2,
        )
        violations, warnings = check_constraints(make_scene([glass, shirt]))
        self.assertEqual(violations, [])
        self.assertEqual(len(warnings), 1)
        w = warnings[0]
        self.assertEqual(w.type, "FRAGILE_LOAD")
        self.assertEqual(w.object_id, "glass")
        self.assertAlmostEqual(w.details["supported_weight_kg"], 0.2)


class TestNoOpDefault(unittest.TestCase):
    def test_default_constraints_object_untouched_in_busy_scene(self):
        plain = Object(
            id="plain",
            dimensions=(0.2, 0.2, 0.2),
            position=(0.0, 0.1, 0.0),
        )
        stacked_on_plain = Object(
            id="stacked",
            dimensions=(0.2, 0.2, 0.2),
            position=(0.0, 0.3 + 0.01, 0.0),
            mass_kg=5.0,
        )
        tilted_plain = Object(
            id="tilted",
            dimensions=(0.1, 0.3, 0.1),
            position=(1.0, 0.05, 0.0),
            rotation=quat_x_rot(90.0),
        )
        violations, warnings = check_constraints(make_scene([plain, stacked_on_plain, tilted_plain]))
        for item in violations + warnings:
            self.assertNotEqual(item.object_id, "plain")
            self.assertNotEqual(item.object_id, "tilted")


class TestHeavyOnTop(unittest.TestCase):
    def test_heavy_object_stacked_produces_warning(self):
        base = Object(
            id="base",
            dimensions=(0.3, 0.2, 0.3),
            position=(0.0, 0.1, 0.0),
        )
        heavy_box = Object(
            id="heavybox",
            dimensions=(0.2, 0.1, 0.2),
            position=(0.0, 0.2 + 0.05, 0.0),
            mass_kg=8.0,
            constraints=Constraints(heavy=True),
        )
        _, warnings = check_constraints(make_scene([base, heavy_box]))
        heavy_warnings = [w for w in warnings if w.type == "HEAVY_ON_TOP"]
        self.assertEqual(len(heavy_warnings), 1)
        self.assertEqual(heavy_warnings[0].object_id, "heavybox")
        self.assertIn("base", heavy_warnings[0].details["resting_on"])


if __name__ == "__main__":
    unittest.main()
