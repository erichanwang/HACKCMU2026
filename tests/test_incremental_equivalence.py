"""`PlacementValidator`'s exact mode must equal `validate_layout`, bit for bit.

`physics.incremental.PlacementValidator.validate()` / `.try_place_exact()`
maintain the whole-scene verdict incrementally -- only the objects whose
verdict can actually change are re-derived on an add or a remove. That is only
useful if the answer is *identical* to running the full pipeline, so this file
is the differential harness: a few hundred seeded random scenes (mixed
rigidities, random orientations, every constraint flag) run through both paths
and compared as JSON.

Also pins the two bugs an earlier differential run found:
  - asymmetric support tracking (a fragile pedestal slid under an existing
    load), which the approximate `try_place` used to miss entirely;
  - FRAGILE_LOAD's `adjacent_heavy` ignoring height, so a heavy item a metre
    above a fragile one counted as "adjacent".
"""
from __future__ import annotations

import random
import unittest

import numpy as np

from physics.incremental import PlacementValidator, _precompute_row
from physics.io import result_to_json
from physics.scene_geometry import precompute
from physics.schema import Constraints, Container, Object, Scene
from physics.validator import validate_layout

NUM_SCENES = 300
LOCKS = (None, None, None, "this_side_up", "flat_only", "horizontal")


def _rand_object(rng: random.Random, oid: str, placed: list[Object]) -> Object:
    dims = (rng.uniform(0.05, 0.30), rng.uniform(0.05, 0.30), rng.uniform(0.05, 0.30))
    mode = rng.random()
    if mode < 0.5:  # on the floor
        y = dims[1] / 2.0
    elif mode < 0.75 and placed:  # flush on top of an earlier box
        under = rng.choice(placed)
        y = under.position[1] + under.dimensions[1] / 2.0 + dims[1] / 2.0
    else:  # anywhere (floating / interpenetrating / poking through a wall)
        y = rng.uniform(0.0, 0.8)
    rotation = (0.0, 0.0, 0.0, 1.0)
    if rng.random() < 0.3:
        q = np.array([rng.uniform(-1.0, 1.0) for _ in range(4)])
        n = float(np.linalg.norm(q))
        if n > 1e-6:
            rotation = tuple((q / n).tolist())
    return Object(
        id=oid,
        dimensions=dims,
        position=(rng.uniform(-0.5, 0.5), y, rng.uniform(-0.3, 0.3)),
        rotation=rotation,
        mass_kg=rng.uniform(0.1, 5.0),
        constraints=Constraints(
            fragile=rng.random() < 0.2,
            keep_upright=rng.random() < 0.1,
            cannot_support_weight=rng.random() < 0.2,
            heavy=rng.random() < 0.2,
            orientation_lock=rng.choice(LOCKS),
        ),
        rigidity=rng.choice(["rigid", "rigid", "soft", "semi"]),
        compressibility_k=rng.uniform(1.0, 1.5),
    )


def random_scene(rng: random.Random) -> Scene:
    container = Container(id="container", dimensions=(1.0, 0.8, 0.6), position=(0.0, 0.4, 0.0))
    objects: list[Object] = []
    for i in range(rng.randint(1, 9)):
        objects.append(_rand_object(rng, f"o{i}", objects))
    return Scene(container=container, objects=objects)


class TestExactEquivalence(unittest.TestCase):
    def test_commit_all_then_validate(self):
        rng = random.Random(7)
        for trial in range(NUM_SCENES):
            scene = random_scene(rng)
            pv = PlacementValidator(scene.container)
            for obj in scene.objects:
                pv.commit(obj)
            with self.subTest(trial=trial, n=len(scene.objects)):
                self.assertEqual(
                    result_to_json(pv.validate()), result_to_json(validate_layout(scene))
                )

    def test_adds_interleaved_with_removes(self):
        """Removal has to undo exactly what the matching add did."""
        rng = random.Random(99)
        for trial in range(NUM_SCENES // 3):
            scene = random_scene(rng)
            pv = PlacementValidator(scene.container)
            live: list[Object] = []
            for obj in scene.objects:
                pv.commit(obj)
                live.append(obj)
                if len(live) > 2 and rng.random() < 0.35:
                    pv.remove(live.pop(rng.randrange(len(live))).id)
                with self.subTest(trial=trial, k=len(live)):
                    self.assertEqual(
                        result_to_json(pv.validate()),
                        result_to_json(
                            validate_layout(Scene(container=scene.container, objects=list(live)))
                        ),
                    )

    def test_try_place_exact_matches_and_does_not_commit(self):
        rng = random.Random(5)
        for trial in range(2 * NUM_SCENES // 3):
            scene = random_scene(rng)
            base, candidate = scene.objects[:-1], scene.objects[-1]
            pv = PlacementValidator(scene.container)
            for obj in base:
                pv.commit(obj)
            before = result_to_json(pv.validate())
            got = result_to_json(pv.try_place_exact(candidate))
            want = result_to_json(
                validate_layout(Scene(container=scene.container, objects=base + [candidate]))
            )
            with self.subTest(trial=trial, n=len(scene.objects)):
                self.assertEqual(got, want)
                self.assertEqual(pv.placed_ids, [o.id for o in base])
                self.assertEqual(result_to_json(pv.validate()), before)

    def test_metrics_false_changes_only_the_metrics_key(self):
        rng = random.Random(11)
        scene = random_scene(rng)
        pv = PlacementValidator(scene.container)
        for obj in scene.objects:
            pv.commit(obj)
        full, lean = pv.validate(), pv.validate(metrics=False)
        self.assertEqual(lean["metrics"], {})
        for key in ("valid", "score", "violations", "warnings"):
            self.assertEqual(lean[key], full[key])

    def test_malformed_candidate_matches_full_pipeline(self):
        container = Container(id="c", dimensions=(1.0, 1.0, 1.0))
        good = Object(id="a", dimensions=(0.2, 0.2, 0.2), position=(0.0, -0.4, 0.0))
        for bad in (
            Object(id="b", dimensions=(0.2, float("nan"), 0.2), position=(0.0, -0.4, 0.0)),
            Object(id="b", dimensions=(0.2, 0.0, 0.2), position=(0.0, -0.4, 0.0)),
            Object(id="b", dimensions=(0.2, 0.2, 0.2), position=(0.0, float("inf"), 0.0)),
            Object(id="b", dimensions=(0.2, 0.2, 0.2), position=(0.0, -0.4, 0.0),
                   rotation=(0.0, 0.0, 0.0, 0.0)),
            Object(id="a", dimensions=(0.2, 0.2, 0.2), position=(0.3, -0.4, 0.0)),  # duplicate
        ):
            pv = PlacementValidator(container)
            pv.commit(good)
            with self.subTest(bad=bad.dimensions):
                self.assertEqual(
                    result_to_json(pv.try_place_exact(bad)),
                    result_to_json(validate_layout(Scene(container=container,
                                                         objects=[good, bad]))),
                )
                self.assertEqual(pv.placed_ids, ["a"])


class TestPrecomputeRowIsBitIdentical(unittest.TestCase):
    """The exact path rebuilds precompute's rows one object at a time. If that
    ever drifts by an ulp, depths and hull areas drift with it."""

    def test_single_object_rows_match_the_batched_precompute(self):
        rng = random.Random(3)
        scene = random_scene(rng)
        while len(scene.objects) < 6:
            scene = random_scene(rng)
        geom = precompute(scene)
        for i, obj in enumerate(scene.objects):
            axes, half_extents, center, vertices = _precompute_row(obj)
            with self.subTest(obj=obj.id):
                self.assertTrue(np.array_equal(axes, geom.axes[i]))
                self.assertTrue(np.array_equal(half_extents, geom.half_extents[i]))
                self.assertTrue(np.array_equal(center, geom.centers[i]))
                self.assertTrue(np.array_equal(vertices, geom.vertices[i]))


def _pedestal_scene():
    """C is a pedestal under B's left half; A fills the gap under B's right
    half. A is fragile and cannot support weight, so placing it puts half of
    B's 10 kg onto something that must not be loaded."""
    container = Container(id="bin", dimensions=(3.0, 2.0, 1.0), position=(0.0, 1.0, 0.0))
    c = Object(id="C", dimensions=(1.0, 0.5, 1.0), position=(-0.5, 0.25, 0.0), mass_kg=1.0)
    b = Object(id="B", dimensions=(2.0, 0.2, 1.0), position=(0.0, 0.6, 0.0), mass_kg=10.0)
    a = Object(
        id="A",
        dimensions=(1.0, 0.5, 1.0),
        position=(0.5, 0.25, 0.0),
        mass_kg=1.0,
        constraints=Constraints(cannot_support_weight=True),
    )
    return container, c, b, a


class TestAsymmetricSupportRegression(unittest.TestCase):
    """The new object as a SUPPORTER, not just as something being supported."""

    def test_exact_path_matches_full_validator(self):
        container, c, b, a = _pedestal_scene()
        pv = PlacementValidator(container)
        pv.commit(c)
        pv.commit(b)
        self.assertEqual(
            result_to_json(pv.try_place_exact(a)),
            result_to_json(validate_layout(Scene(container=container, objects=[c, b, a]))),
        )

    def test_approximate_try_place_no_longer_says_yes(self):
        container, c, b, a = _pedestal_scene()
        pv = PlacementValidator(container)
        self.assertTrue(pv.place(c)["valid"])
        self.assertTrue(pv.place(b)["valid"])
        result = pv.try_place(a)
        self.assertFalse(result["valid"], result)
        overload = next(
            v for v in result["violations"] if v["type"] == "FRAGILE_OBJECT_OVERLOADED"
        )
        self.assertEqual(overload["object"], "A")
        self.assertGreater(overload["supported_weight_kg"], 0.0)


class TestAdjacentHeavyHeightRegression(unittest.TestCase):
    """FRAGILE_LOAD's `adjacent_heavy` means beside, not anywhere overhead."""

    @staticmethod
    def _scene(heavy_y: float):
        container = Container(id="bin", dimensions=(2.0, 2.0, 1.0), position=(0.0, 1.0, 0.0))
        fragile = Object(
            id="fragile",
            dimensions=(0.3, 0.1, 0.3),
            position=(0.0, 0.05, 0.0),
            constraints=Constraints(fragile=True),
        )
        heavy = Object(
            id="heavy",
            dimensions=(0.3, 0.1, 0.3),
            position=(0.0, heavy_y, 0.0),
            mass_kg=8.0,
            constraints=Constraints(heavy=True),
        )
        return container, fragile, heavy

    def _adjacent_flags(self, heavy_y: float):
        container, fragile, heavy = self._scene(heavy_y)
        full = validate_layout(Scene(container=container, objects=[fragile, heavy]))
        pv = PlacementValidator(container)
        pv.commit(fragile)
        incremental = pv.try_place_exact(heavy)
        self.assertEqual(result_to_json(incremental), result_to_json(full))
        return [w["adjacent_heavy"] for w in full["warnings"] if w["type"] == "FRAGILE_LOAD"]

    def test_heavy_far_above_is_not_adjacent(self):
        self.assertEqual(self._adjacent_flags(1.85), [])

    def test_heavy_beside_at_the_same_height_still_is(self):
        container = Container(id="bin", dimensions=(2.0, 2.0, 1.0), position=(0.0, 1.0, 0.0))
        fragile = Object(
            id="fragile",
            dimensions=(0.3, 0.1, 0.3),
            position=(0.0, 0.05, 0.0),
            constraints=Constraints(fragile=True),
        )
        # overlapping footprint, resting flush on top -> still "adjacent"
        heavy = Object(
            id="heavy",
            dimensions=(0.3, 0.1, 0.3),
            position=(0.0, 0.15, 0.0),
            mass_kg=8.0,
            constraints=Constraints(heavy=True),
        )
        full = validate_layout(Scene(container=container, objects=[fragile, heavy]))
        flags = [w["adjacent_heavy"] for w in full["warnings"] if w["type"] == "FRAGILE_LOAD"]
        self.assertEqual(flags, [True])
        pv = PlacementValidator(container)
        pv.commit(fragile)
        self.assertEqual(result_to_json(pv.try_place_exact(heavy)), result_to_json(full))


if __name__ == "__main__":
    unittest.main()
