import math
import random
import unittest

import numpy as np

from physics.collision import aabb_overlap
from physics.geometry import obb_from, obb_vertices, quat_to_matrix
from physics.scene_geometry import (
    MalformedSceneError,
    aabb_candidate_pairs,
    check_no_duplicate_ids,
    on_floor,
    precompute,
    resting_pairs,
)
from physics.schema import Container, Object, Scene
from physics.support import check_support
from tests import fixtures


def _random_scene(rng: random.Random, n: int, unit_quats: bool = True) -> Scene:
    objs = []
    for i in range(n):
        q = [rng.gauss(0, 1) for _ in range(4)]
        norm = math.sqrt(sum(c * c for c in q))
        q = [c / norm for c in q]
        if not unit_quats:
            q = [c * rng.uniform(0.5, 2.0) for c in q]  # exercises the re-normalization branch
        objs.append(Object(
            id=f"o{i}",
            dimensions=tuple(rng.uniform(0.02, 0.5) for _ in range(3)),
            position=tuple(rng.uniform(-1, 1) for _ in range(3)),
            rotation=tuple(q),
        ))
    return Scene(Container("c", (3.0, 3.0, 3.0)), objs)


class TestPrecomputeMatchesGeometry(unittest.TestCase):
    def test_axes_and_vertices_match_single_object_path(self):
        rng = random.Random(7)
        for unit in (True, False):
            for trial in range(40):
                scene = _random_scene(rng, 6, unit_quats=unit)
                g = precompute(scene)
                for i, o in enumerate(scene.objects):
                    ref = obb_from(o)
                    np.testing.assert_allclose(g.axes[i], quat_to_matrix(o.rotation), atol=1e-12)
                    np.testing.assert_allclose(g.axes[i], ref.axes, atol=1e-12)
                    np.testing.assert_allclose(g.vertices[i], obb_vertices(ref), atol=1e-12)
                    np.testing.assert_allclose(g.obbs[i].half_extents, ref.half_extents)
                np.testing.assert_allclose(g.aabb_min, g.vertices.min(axis=1))
                np.testing.assert_allclose(g.aabb_max, g.vertices.max(axis=1))

    def test_empty_scene(self):
        g = precompute(Scene(Container("c", (1, 1, 1)), []))
        self.assertEqual(g.n, 0)
        self.assertEqual(aabb_candidate_pairs(g).shape, (0, 2))
        self.assertEqual(resting_pairs(g, 1e-3), [])
        self.assertEqual(on_floor(g, 1e-3).shape, (0,))


class TestMalformed(unittest.TestCase):
    def _scene(self, **kw) -> Scene:
        good = Object("good", (0.1, 0.1, 0.1), (0, 0.05, 0))
        bad = Object("bad", kw.pop("dimensions", (0.1, 0.1, 0.1)), kw.pop("position", (0.3, 0.05, 0)), **kw)
        return Scene(Container("c", (1, 1, 1), (0, 0.5, 0)), [good, bad])

    def test_names_the_offending_object(self):
        cases = [
            dict(dimensions=(float("nan"), 0.1, 0.1)),
            dict(dimensions=(0.0, 0.1, 0.1)),
            dict(dimensions=(-0.1, 0.1, 0.1)),
            dict(position=(float("inf"), 0, 0)),
            dict(rotation=(0.0, 0.0, 0.0, 0.0)),
            dict(rotation=(float("nan"), 0, 0, 1)),
            dict(dimensions=(0.1, 0.1)),
        ]
        for kw in cases:
            with self.subTest(kw=kw), self.assertRaises(MalformedSceneError) as cm:
                precompute(self._scene(**kw))
            self.assertEqual(cm.exception.object_id, "bad")
            self.assertIsInstance(cm.exception, ValueError)

    def test_duplicate_ids(self):
        scene = Scene(Container("c", (1, 1, 1)), [Object("a", (0.1,) * 3, (0, 0, 0)), Object("a", (0.1,) * 3, (0.5, 0, 0))])
        with self.assertRaises(MalformedSceneError) as cm:
            check_no_duplicate_ids(scene)
        self.assertEqual(cm.exception.object_id, "a")
        with self.assertRaises(MalformedSceneError):
            precompute(scene)

    def test_bad_container(self):
        with self.assertRaises(MalformedSceneError) as cm:
            precompute(Scene(Container("box", (1, float("nan"), 1)), []))
        self.assertEqual(cm.exception.object_id, "box")


class TestSharedTopology(unittest.TestCase):
    def test_candidate_pairs_match_brute_force(self):
        rng = random.Random(3)
        for _ in range(30):
            g = precompute(_random_scene(rng, 12))
            brute = {(i, j) for i in range(g.n) for j in range(i + 1, g.n) if aabb_overlap(g.obbs[i], g.obbs[j])}
            self.assertEqual(brute, {tuple(p) for p in aabb_candidate_pairs(g).tolist()})

    def test_resting_pairs_agree_with_support(self):
        for fn in (fixtures.valid_packed_scene, fixtures.scene_nearly_full, fixtures.scene_stacked_objects,
                   fixtures.scene_with_precarious_balance, fixtures.scene_with_floating_object):
            scene = fn()
            g = precompute(scene)
            mine: dict[str, set[str]] = {oid: set() for oid in g.ids}
            for t, b, _ in resting_pairs(g, 1e-3):
                mine[g.ids[t]].add(g.ids[b])
            floor = {g.ids[i] for i in np.nonzero(on_floor(g, 1e-3))[0]}
            for r in check_support(scene, geom=g):
                self.assertEqual(set(r.supporting_objects) - {"container_floor"}, mine[r.object_id], fn.__name__)
                self.assertEqual("container_floor" in r.supporting_objects, r.object_id in floor, fn.__name__)


if __name__ == "__main__":
    unittest.main()
