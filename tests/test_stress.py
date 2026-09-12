"""Seeded property-based stress tests for the physics layer.

Run: python3 -m unittest tests.test_stress -v

All randomness comes from `random.Random(seed)` with fixed integer seeds --
fully deterministic, no wall-clock/OS entropy. Each property is run across
many seeded random scenes/pairs (SCENES_PER_PROPERTY / PAIRS_PER_PROPERTY)
rather than relying on a single lucky draw.
"""
from __future__ import annotations

import math
import random
import unittest

import numpy as np

from physics.collision import check_collision
from physics.constraints import check_constraints
from physics.containment import check_containment, check_scene_containment
from physics.geometry import obb_from
from physics.schema import Constraints, Container, Object, Scene
from physics.support import check_support

SEEDS = list(range(20))
SCENES_PER_PROPERTY = 100  # trials per property (>= the 50-200 asked for)


# ---------------------------------------------------------------------------
# Random scene generator
# ---------------------------------------------------------------------------

def random_unit_quat(rng: random.Random) -> tuple[float, float, float, float]:
    """Random unit quaternion via normalized random 4-vector, rejecting
    near-zero norms (the axis+angle route is equivalent; this is simpler)."""
    while True:
        x, y, z, w = (rng.uniform(-1.0, 1.0) for _ in range(4))
        n = math.sqrt(x * x + y * y + z * z + w * w)
        if n > 1e-6:
            return (x / n, y / n, z / n, w / n)


def random_dims(rng: random.Random, lo=0.02, hi=0.5):
    return (rng.uniform(lo, hi), rng.uniform(lo, hi), rng.uniform(lo, hi))


def random_position(rng: random.Random, lo=-1.0, hi=1.0):
    return (rng.uniform(lo, hi), rng.uniform(lo, hi), rng.uniform(lo, hi))


def random_constraints(rng: random.Random) -> Constraints:
    """Occasionally-True constraint fields, not always."""
    lock = None
    if rng.random() < 0.2:
        lock = rng.choice(["this_side_up", "flat_only", "horizontal"])
    return Constraints(
        fragile=rng.random() < 0.2,
        keep_upright=rng.random() < 0.2,
        cannot_support_weight=rng.random() < 0.2,
        heavy=rng.random() < 0.2,
        orientation_lock=lock,
    )


def random_object(rng: random.Random, idx: int) -> Object:
    return Object(
        id=f"obj{idx}",
        dimensions=random_dims(rng),
        position=random_position(rng),
        rotation=random_unit_quat(rng),
        mass_kg=rng.uniform(0.05, 5.0),
        constraints=random_constraints(rng),
    )


def random_container(rng: random.Random) -> Container:
    # Container big enough that random objects plausibly fit/overlap it,
    # but still a "syntactically valid" random box per the task spec.
    return Container(
        id="container",
        dimensions=(rng.uniform(0.5, 2.0), rng.uniform(0.5, 2.0), rng.uniform(0.5, 2.0)),
        position=(0.0, 0.0, 0.0),
        rotation=random_unit_quat(rng),
    )


def random_scene(rng: random.Random, n_objects: int = 5) -> Scene:
    container = random_container(rng)
    objects = [random_object(rng, i) for i in range(n_objects)]
    return Scene(container=container, objects=objects)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class NoCrashesOnValidInput(unittest.TestCase):
    """Property 1: valid random input never raises in any of the five modules."""

    def test_no_crashes(self):
        for seed in SEEDS:
            rng = random.Random(seed)
            for _ in range(SCENES_PER_PROPERTY // len(SEEDS) + 1):
                scene = random_scene(rng, n_objects=5)
                with self.subTest(seed=seed):
                    obbs = [obb_from(scene.container)] + [obb_from(o) for o in scene.objects]
                    for i in range(len(obbs)):
                        for j in range(len(obbs)):
                            if i != j:
                                check_collision(obbs[i], obbs[j])
                    check_scene_containment(scene)
                    check_support(scene)
                    check_constraints(scene)


class CollisionSymmetry(unittest.TestCase):
    """Property 2: check_collision(a, b) and check_collision(b, a) agree on
    `colliding` and `penetration_depth_m`, and their axes are anti-parallel."""

    def test_symmetry(self):
        for seed in SEEDS:
            rng = random.Random(seed)
            for _ in range(SCENES_PER_PROPERTY // len(SEEDS) + 1):
                a = Object(id="a", dimensions=random_dims(rng), position=random_position(rng),
                           rotation=random_unit_quat(rng))
                b = Object(id="b", dimensions=random_dims(rng), position=random_position(rng),
                           rotation=random_unit_quat(rng))
                oa, ob = obb_from(a), obb_from(b)
                r_ab = check_collision(oa, ob)
                r_ba = check_collision(ob, oa)
                with self.subTest(seed=seed):
                    self.assertEqual(r_ab.colliding, r_ba.colliding)
                    self.assertAlmostEqual(r_ab.penetration_depth_m, r_ba.penetration_depth_m, places=9)
                    if r_ab.colliding:
                        # axis_ab should point opposite to axis_ba (both are
                        # oriented "from first arg's center towards second's")
                        dot = float(np.dot(r_ab.axis, r_ba.axis))
                        self.assertLess(dot, -1.0 + 1e-6)


class SelfCollision(unittest.TestCase):
    """Property 3: an OBB always collides with an identical copy of itself."""

    def test_self_collision(self):
        for seed in SEEDS:
            rng = random.Random(seed)
            for _ in range(SCENES_PER_PROPERTY // len(SEEDS) + 1):
                dims = random_dims(rng)
                pos = random_position(rng)
                rot = random_unit_quat(rng)
                a = Object(id="a", dimensions=dims, position=pos, rotation=rot)
                b = Object(id="b", dimensions=dims, position=pos, rotation=rot)
                oa, ob = obb_from(a), obb_from(b)
                result = check_collision(oa, ob)
                with self.subTest(seed=seed):
                    self.assertTrue(result.colliding)
                    self.assertGreater(result.penetration_depth_m, 0.0)


class NoOverlapUnderTranslation(unittest.TestCase):
    """Property 4: two same-sized, non-rotated boxes separated beyond the sum
    of half-extents along a single world axis never collide."""

    def test_far_apart_no_collision(self):
        for seed in SEEDS:
            rng = random.Random(seed)
            for _ in range(SCENES_PER_PROPERTY // len(SEEDS) + 1):
                dims = random_dims(rng)
                pos = random_position(rng)
                axis = rng.randrange(3)
                half = dims[axis] / 2.0
                far_pos = list(pos)
                far_pos[axis] += 2 * half + 1.0  # well beyond half+half, no rotation
                a = Object(id="a", dimensions=dims, position=pos)
                b = Object(id="b", dimensions=dims, position=tuple(far_pos))
                result = check_collision(obb_from(a), obb_from(b))
                with self.subTest(seed=seed, axis=axis):
                    self.assertFalse(result.colliding)


class ContainmentSelfConsistency(unittest.TestCase):
    """Property 5: an object centered in the container, strictly smaller in
    every axis, with the container's own rotation, is always contained with
    zero penetrating vertices."""

    def test_centered_smaller_object_contained(self):
        for seed in SEEDS:
            rng = random.Random(seed)
            for _ in range(SCENES_PER_PROPERTY // len(SEEDS) + 1):
                container_dims = random_dims(rng, lo=0.3, hi=1.0)
                rot = random_unit_quat(rng)
                container = Container(id="c", dimensions=container_dims, position=(0.0, 0.0, 0.0), rotation=rot)
                # strictly smaller in every axis
                obj_dims = tuple(d * rng.uniform(0.1, 0.9) for d in container_dims)
                obj = Object(id="o", dimensions=obj_dims, position=(0.0, 0.0, 0.0), rotation=rot)
                result = check_containment(obb_from(container), obb_from(obj))
                with self.subTest(seed=seed):
                    self.assertTrue(result.contained)
                    self.assertEqual(result.penetrating_vertices, [])


class MalformedInputRejected(unittest.TestCase):
    """Property 6: obb_from raises ValueError for NaN/inf/zero/negative dims
    and zero-norm quaternions -- never silently returns a bogus OBB."""

    def test_bad_dimensions_rejected(self):
        bad_values = [float("nan"), float("inf"), float("-inf"), 0.0, -0.01]
        for seed in SEEDS:
            rng = random.Random(seed)
            for _ in range(20):
                dims = list(random_dims(rng))
                axis = rng.randrange(3)
                dims[axis] = rng.choice(bad_values)
                obj = Object(id="bad", dimensions=tuple(dims), position=random_position(rng))
                with self.subTest(seed=seed, bad_dim=dims[axis]):
                    with self.assertRaises(ValueError):
                        obb_from(obj)

    def test_zero_norm_quaternion_rejected(self):
        for seed in SEEDS:
            rng = random.Random(seed)
            obj = Object(id="bad", dimensions=random_dims(rng), position=random_position(rng),
                          rotation=(0.0, 0.0, 0.0, 0.0))
            with self.subTest(seed=seed):
                with self.assertRaises(ValueError):
                    obb_from(obj)

    def test_nonfinite_position_rejected(self):
        for seed in SEEDS:
            rng = random.Random(seed)
            for bad in (float("nan"), float("inf"), float("-inf")):
                pos = list(random_position(rng))
                pos[rng.randrange(3)] = bad
                obj = Object(id="bad", dimensions=random_dims(rng), position=tuple(pos))
                with self.subTest(seed=seed, bad=bad):
                    with self.assertRaises(ValueError):
                        obb_from(obj)


class SupportRatioBounds(unittest.TestCase):
    """Property 7: SupportResult.support_ratio is always within [0.0, 1.0]."""

    def test_support_ratio_bounds(self):
        for seed in SEEDS:
            rng = random.Random(seed)
            for _ in range(SCENES_PER_PROPERTY // len(SEEDS) + 1):
                scene = random_scene(rng, n_objects=6)
                results = check_support(scene)
                with self.subTest(seed=seed):
                    for r in results:
                        self.assertGreaterEqual(r.support_ratio, 0.0)
                        self.assertLessEqual(r.support_ratio, 1.0)


class Determinism(unittest.TestCase):
    """Property 8: running the same check twice on the same input gives
    bit-identical results -- no hidden randomness/iteration-order dependence."""

    def test_deterministic_repeats(self):
        for seed in SEEDS:
            rng = random.Random(seed)
            scene = random_scene(rng, n_objects=5)

            obbs = [obb_from(scene.container)] + [obb_from(o) for o in scene.objects]
            for i in range(len(obbs)):
                for j in range(len(obbs)):
                    if i == j:
                        continue
                    r1 = check_collision(obbs[i], obbs[j])
                    r2 = check_collision(obbs[i], obbs[j])
                    with self.subTest(seed=seed, pair=(i, j)):
                        self.assertEqual(r1.colliding, r2.colliding)
                        self.assertEqual(r1.penetration_depth_m, r2.penetration_depth_m)
                        if r1.colliding:
                            self.assertTrue(np.array_equal(r1.axis, r2.axis))

            c1 = check_scene_containment(scene)
            c2 = check_scene_containment(scene)
            self.assertEqual([r.contained for r in c1], [r.contained for r in c2])
            self.assertEqual(
                [r.penetration_depth_m for r in c1], [r.penetration_depth_m for r in c2]
            )

            s1 = check_support(scene)
            s2 = check_support(scene)
            self.assertEqual(
                [(r.support_ratio, r.stability_margin_m, r.floating, r.unstable) for r in s1],
                [(r.support_ratio, r.stability_margin_m, r.floating, r.unstable) for r in s2],
            )

            v1, w1 = check_constraints(scene)
            v2, w2 = check_constraints(scene)
            self.assertEqual([(v.type, v.object_id) for v in v1], [(v.type, v.object_id) for v in v2])
            self.assertEqual([(w.type, w.object_id) for w in w1], [(w.type, w.object_id) for w in w2])


if __name__ == "__main__":
    unittest.main()
