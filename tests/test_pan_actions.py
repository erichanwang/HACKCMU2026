import math
import unittest
from dataclasses import replace

from physics.geometry import quat_to_matrix
from physics.schema import Container
from physics.support import SupportResult, check_support
from pan.actions import (
    closing_phrase,
    current_location_phrase,
    describe_action,
    describe_sequence,
    fill_action_text,
    humanize_label,
    layer_phrase,
    orientation_phrase,
    rotation_phrase,
    target_region,
)
from pan.types import PackingAction, apply_action
from tests.fixtures import valid_packed_scene

CONTAINER = Container(id="carry_on", dimensions=(0.56, 0.23, 0.36), position=(0.0, 0.115, 0.0))


def _quat_y(deg: float) -> tuple[float, float, float, float]:
    half = math.radians(deg) / 2.0
    return (0.0, math.sin(half), 0.0, math.cos(half))


def _shoe_moved_outside(scene):
    """valid_packed_scene with the shoe shifted to x=-0.6 (outside, to the left)."""
    objects = [
        replace(o, position=(-0.6, 0.055, 0.10)) if o.id == "shoe" else o for o in scene.objects
    ]
    return replace(scene, objects=objects)


class TestHumanizeLabel(unittest.TestCase):
    """Grounding rule 1: naming."""

    def test_trailing_index_stripped(self):
        self.assertEqual(humanize_label("shoe_01"), "shoe")

    def test_underscore_to_space(self):
        self.assertEqual(humanize_label("headphones_case"), "headphones case")
        self.assertEqual(humanize_label("toiletry_bottle"), "toiletry bottle")

    def test_label_override(self):
        self.assertEqual(
            humanize_label("shoe", {"shoe": "black running shoe"}), "black running shoe"
        )

    def test_missing_from_labels_falls_back(self):
        self.assertEqual(humanize_label("shoe", {"camera": "old camera"}), "shoe")


class TestCurrentLocationPhrase(unittest.TestCase):
    """Grounding rule 2: current location."""

    def test_inside_reports_region(self):
        phrase = current_location_phrase(CONTAINER, (-0.13, 0.055, 0.10))
        self.assertEqual(phrase, "currently in the front-left corner of the suitcase")

    def test_outside_left(self):
        phrase = current_location_phrase(CONTAINER, (-0.6, 0.055, 0.10))
        self.assertEqual(phrase, "currently outside the suitcase to the left of it")

    def test_outside_front(self):
        phrase = current_location_phrase(CONTAINER, (0.0, 0.055, 0.9))
        self.assertEqual(phrase, "currently outside the suitcase to the front of it")


class TestTargetRegion(unittest.TestCase):
    """Grounding rule 3 (grid): nine target points -> nine regions, plus a
    rotated-container case computed by hand."""

    def test_region_grid(self):
        cases = {
            (-0.2, -0.15): "rear-left corner",
            (0.0, -0.15): "rear-center",
            (0.2, -0.15): "rear-right corner",
            (-0.2, 0.0): "middle-left",
            (0.0, 0.0): "center",
            (0.2, 0.0): "middle-right",
            (-0.2, 0.15): "front-left corner",
            (0.0, 0.15): "front-center",
            (0.2, 0.15): "front-right corner",
        }
        for (x, z), expected in cases.items():
            with self.subTest(x=x, z=z):
                self.assertEqual(target_region(CONTAINER, (x, 0.05, z)), expected)

    def test_rotated_container(self):
        # Container yawed +90 deg about Y: its local +x axis now points to
        # world -Z and local +z axis now points to world +X (verified via
        # quat_to_matrix). A world point at (0.15, 0.115, -0.2) is therefore
        # the container-local point (local_x=0.2, local_z=0.15) -> the same
        # "front-right corner" as the unrotated grid test above.
        rotated = replace(CONTAINER, rotation=_quat_y(90.0))
        axes = quat_to_matrix(rotated.rotation)
        self.assertTrue(all(abs(v) < 1e-9 for v in (axes[:, 0] - [0, 0, -1])))
        self.assertTrue(all(abs(v) < 1e-9 for v in (axes[:, 2] - [1, 0, 0])))
        self.assertEqual(target_region(rotated, (0.15, 0.115, -0.2)), "front-right corner")


class TestLayerPhrase(unittest.TestCase):
    """Grounding rule 3 (layer)."""

    def test_floor_only(self):
        support = SupportResult(object_id="shoe", support_ratio=1.0, stability_margin_m=0.1,
                                 supporting_objects=["container_floor"])
        self.assertEqual(layer_phrase(support), "flat on the suitcase floor")

    def test_stacked_on_object(self):
        support = SupportResult(object_id="camera", support_ratio=1.0, stability_margin_m=0.1,
                                 supporting_objects=["toiletry_bag"])
        self.assertEqual(layer_phrase(support), "on top of the toiletry bag")

    def test_unsupported(self):
        support = SupportResult(object_id="charger", support_ratio=0.0, stability_margin_m=-1e6,
                                 supporting_objects=[])
        self.assertEqual(layer_phrase(support), "(unsupported)")

    def test_from_real_fixture_stack(self):
        scene = valid_packed_scene()
        (result,) = [r for r in check_support(scene) if r.object_id == "camera"]
        self.assertEqual(layer_phrase(result), "on top of the toiletry bag")


class TestRotationPhrase(unittest.TestCase):
    """Grounding rule 4, plus a numeric check of the clockwise convention
    independent of rotation_phrase itself (never just assume the sign)."""

    IDENTITY = (0.0, 0.0, 0.0, 1.0)

    def test_negative_yaw_is_clockwise(self):
        phrase = rotation_phrase(self.IDENTITY, _quat_y(-35.0))
        self.assertEqual(phrase, "rotated approximately 35 degrees clockwise (seen from above)")

    def test_positive_yaw_is_counterclockwise(self):
        phrase = rotation_phrase(self.IDENTITY, _quat_y(90.0))
        self.assertEqual(
            phrase, "rotated approximately 90 degrees counterclockwise (seen from above)"
        )

    def test_small_rotation_omitted(self):
        self.assertIsNone(rotation_phrase(self.IDENTITY, _quat_y(2.0)))

    def test_clockwise_sign_convention_numeric(self):
        """Independently verify: a NEGATIVE right-hand-rule yaw about +Y
        rotates local +x from world +X toward world -Z -- which, viewed on a
        top-down page with +X to the right and -Z ("rear") as page-up, is a
        clockwise sweep (right, to down, i.e. toward +Z/"front")."""
        axis_before = quat_to_matrix(self.IDENTITY)[:, 0]
        axis_after = quat_to_matrix(_quat_y(-45.0))[:, 0]
        self.assertTrue(all(abs(v) < 1e-9 for v in (axis_before - [1, 0, 0])))
        # -45 deg about Y moves local +x toward +Z (front), away from -Z
        # (rear): on the page (right=+X, up=-Z) that is right-to-down, i.e.
        # clockwise -- matching rotation_phrase's "clockwise" for negative yaw.
        self.assertGreater(axis_after[2], 0.0)
        self.assertGreater(axis_after[0], 0.0)


class TestOrientationPhrase(unittest.TestCase):
    """Grounding rule 5."""

    IDENTITY = (0.0, 0.0, 0.0, 1.0)

    def test_smallest_dimension_vertical_is_flat(self):
        # shoe: (0.29, 0.11, 0.12) -- Y (0.11) is smallest, and identity
        # rotation keeps local Y vertical.
        self.assertEqual(orientation_phrase((0.29, 0.11, 0.12), self.IDENTITY), "laid flat")

    def test_largest_dimension_vertical_is_standing_on_end(self):
        # Rotate 90 deg about world Z: local +x becomes vertical (world Y),
        # per quat_to_matrix -- verified numerically, not assumed. Local x's
        # extent (0.30) is this object's largest.
        half = math.radians(90.0) / 2.0
        quat_z90 = (0.0, 0.0, math.sin(half), math.cos(half))
        axes_y_component = quat_to_matrix(quat_z90)[1, :]
        self.assertEqual(int(abs(axes_y_component).argmax()), 0)
        self.assertEqual(
            orientation_phrase((0.30, 0.10, 0.10), quat_z90), "standing on end"
        )

    def test_middle_dimension_vertical_is_on_its_side(self):
        half = math.radians(90.0) / 2.0
        quat_z90 = (0.0, 0.0, math.sin(half), math.cos(half))
        self.assertEqual(orientation_phrase((0.20, 0.10, 0.30), quat_z90), "on its side")


class TestClosingPhrase(unittest.TestCase):
    """Grounding rule 6."""

    def test_without_next_action(self):
        self.assertEqual(closing_phrase(), "The other objects remain in their current positions.")

    def test_with_next_action(self):
        next_action = PackingAction(object_id="camera", target_position=(0, 0, 0))
        self.assertEqual(
            closing_phrase(next_action),
            "The other objects remain in their current positions. "
            "The camera will be placed next.",
        )

    def test_with_next_action_and_labels(self):
        next_action = PackingAction(object_id="camera", target_position=(0, 0, 0))
        self.assertIn(
            "old camera", closing_phrase(next_action, {"camera": "old camera"})
        )


class TestDescribeActionGolden(unittest.TestCase):
    """Golden-string tests against tests.fixtures.valid_packed_scene()."""

    def test_shoe_returning_to_fixture_pose(self):
        scene = valid_packed_scene()
        shoe = next(o for o in scene.objects if o.id == "shoe")
        before = _shoe_moved_outside(scene)
        action = PackingAction(
            object_id="shoe", target_position=shoe.position, target_rotation=shoe.rotation
        )
        text = describe_action(before, action)
        expected = (
            "A traveler picks up the shoe, currently outside the suitcase to the left of it, "
            "and places it flat on the suitcase floor in the front-left corner, laid flat. "
            "The other objects remain in their current positions."
        )
        self.assertEqual(text, expected)

    def test_shoe_with_rotation_clause(self):
        # Same scene, but the target orientation is -35 deg about Y (from
        # the before pose's identity rotation) -> "35 degrees clockwise".
        scene = valid_packed_scene()
        shoe = next(o for o in scene.objects if o.id == "shoe")
        before = _shoe_moved_outside(scene)
        action = PackingAction(
            object_id="shoe", target_position=shoe.position, target_rotation=_quat_y(-35.0)
        )
        text = describe_action(before, action)
        expected = (
            "A traveler picks up the shoe, currently outside the suitcase to the left of it, "
            "and places it flat on the suitcase floor in the front-left corner, "
            "rotated approximately 35 degrees clockwise (seen from above), laid flat. "
            "The other objects remain in their current positions."
        )
        self.assertEqual(text, expected)

    def test_stacked_camera_on_toiletry_bag(self):
        scene = valid_packed_scene()
        camera = next(o for o in scene.objects if o.id == "camera")
        action = PackingAction(
            object_id="camera", target_position=camera.position, target_rotation=camera.rotation
        )
        text = describe_action(scene, action)
        self.assertIn("on top of the toiletry bag", text)

    def test_labels_override_present(self):
        scene = valid_packed_scene()
        shoe = next(o for o in scene.objects if o.id == "shoe")
        before = _shoe_moved_outside(scene)
        action = PackingAction(
            object_id="shoe", target_position=shoe.position, target_rotation=shoe.rotation
        )
        text = describe_action(before, action, labels={"shoe": "black running shoe"})
        self.assertIn("black running shoe", text)

    def test_no_labels_never_invents_black(self):
        scene = valid_packed_scene()
        shoe = next(o for o in scene.objects if o.id == "shoe")
        before = _shoe_moved_outside(scene)
        action = PackingAction(
            object_id="shoe", target_position=shoe.position, target_rotation=shoe.rotation
        )
        text = describe_action(before, action)
        self.assertNotIn("black", text)

    def test_determinism(self):
        scene = valid_packed_scene()
        shoe = next(o for o in scene.objects if o.id == "shoe")
        before = _shoe_moved_outside(scene)
        action = PackingAction(
            object_id="shoe", target_position=shoe.position, target_rotation=shoe.rotation
        )
        self.assertEqual(describe_action(before, action), describe_action(before, action))


class TestDescribeSequence(unittest.TestCase):
    def test_second_step_reflects_first_action(self):
        scene = valid_packed_scene()
        shoe = next(o for o in scene.objects if o.id == "shoe")
        camera = next(o for o in scene.objects if o.id == "camera")
        before = _shoe_moved_outside(scene)
        a1 = PackingAction(
            object_id="shoe", target_position=shoe.position, target_rotation=shoe.rotation
        )
        a2 = PackingAction(
            object_id="camera", target_position=camera.position, target_rotation=camera.rotation
        )
        texts = describe_sequence(before, [a1, a2])
        self.assertEqual(len(texts), 2)
        self.assertIn("currently outside the suitcase to the left of it", texts[0])
        self.assertIn("The camera will be placed next.", texts[0])
        # After a1 is applied, the shoe is back in the suitcase -- the
        # second step's "currently" clause must not still call it outside.
        self.assertNotIn("outside", texts[1])
        self.assertIn("currently in the front-right corner of the suitcase", texts[1])


class TestFillActionText(unittest.TestCase):
    def test_returns_new_object_with_text_set(self):
        scene = valid_packed_scene()
        shoe = next(o for o in scene.objects if o.id == "shoe")
        before = _shoe_moved_outside(scene)
        action = PackingAction(
            object_id="shoe", target_position=shoe.position, target_rotation=shoe.rotation
        )
        filled = fill_action_text(before, action)
        self.assertNotEqual(filled.text, "")
        self.assertEqual(filled.text, describe_action(before, action))
        # original left untouched
        self.assertEqual(action.text, "")
        self.assertIsNot(filled, action)


if __name__ == "__main__":
    unittest.main()
