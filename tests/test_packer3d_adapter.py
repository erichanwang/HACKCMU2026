"""Tests for `physics.packer3d_adapter` (packer3d solver JSON <-> physics scenes).

Everything runs offline against the checked-in solver examples
(`packer3d/examples/*.json`) -- the adapter never calls the solver.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

import numpy as np

from physics.containment import check_scene_containment
from physics.geometry import obb_from, obb_vertices
from physics.io import apply_placements
from physics.metrics import scene_metrics
from physics.packer3d_adapter import (
    IDENTITY_ROTATION,
    TABLE_GAP_M,
    packer3d_placement_from_object,
    physics_point,
    placements_from_packer3d,
    rotation_from_orientation,
    scene_from_packer3d,
    scene_from_packer3d_scenario,
    swap_yz,
    validate_packer3d,
)
from physics.scene_geometry import precompute
from physics.schema import Constraints, Object, Scene

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = REPO_ROOT / "packer3d" / "examples"


def _load(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text())


SUITCASE_RESULT = _load("suitcase_result.json")
SUITCASE_SCENARIO = _load("suitcase.json")
DRAGON_RESULT = _load("dragon_result.json")
DRAGON_SCENARIO = _load("dragon_resupply.json")


class MappingTests(unittest.TestCase):
    def test_object_round_trip_through_packer3d_placement(self):
        obj = Object(
            id="books_1",
            dimensions=(0.24, 0.1, 0.17),
            position=(0.31, 0.05, -0.42),
            mass_kg=3.0,
            constraints=Constraints(fragile=True, cannot_support_weight=True),
        )
        p = packer3d_placement_from_object(obj)
        # center == position + dims/2 (packer3d's own invariant)
        for k in range(3):
            self.assertAlmostEqual(p["center"][k], p["position"][k] + p["dims"][k] / 2.0, places=12)

        back = scene_from_packer3d(
            {"strategy": "x", "container": {"id": "c", "dims": [1.0, 1.0, 1.0]}, "placements": [p]}
        )[0].objects[0]
        self.assertEqual(back.id, obj.id)
        for k in range(3):
            self.assertAlmostEqual(back.dimensions[k], obj.dimensions[k], delta=1e-12)
            self.assertAlmostEqual(back.position[k], obj.position[k], delta=1e-12)
        self.assertEqual(back.rotation, IDENTITY_ROTATION)
        self.assertAlmostEqual(back.mass_kg, obj.mass_kg, delta=1e-12)
        self.assertTrue(back.constraints.fragile and back.constraints.cannot_support_weight)

    def test_container_mapping(self):
        scene, _ = scene_from_packer3d(SUITCASE_RESULT, strategy="naive")
        L, W, H = SUITCASE_RESULT["naive"]["container"]["dims"]
        self.assertEqual(scene.container.dimensions, (L, H, W))
        self.assertEqual(scene.container.position, (L / 2.0, H / 2.0, -W / 2.0))
        # floor at Y=0, min-x wall at X=0, Z extent [-W, 0]
        v = obb_vertices(obb_from(scene.container))
        self.assertAlmostEqual(float(v[:, 1].min()), 0.0, delta=1e-12)
        self.assertAlmostEqual(float(v[:, 0].min()), 0.0, delta=1e-12)
        self.assertAlmostEqual(float(v[:, 2].min()), -W, delta=1e-12)
        self.assertAlmostEqual(float(v[:, 2].max()), 0.0, delta=1e-12)

    def test_com_matches_the_solvers_own_metric(self):
        """Sanity check on the whole mapping: our mass-weighted COM must be the
        solver's COM sent through `physics_point`."""
        for strategy in ("naive", "optimized"):
            scene, extras = scene_from_packer3d(SUITCASE_RESULT, strategy=strategy)
            com = scene_metrics(precompute(scene))["center_of_mass"]
            expected = physics_point(*extras["metrics"]["com"])
            for k in range(3):
                self.assertAlmostEqual(com[k], expected[k], delta=1e-9, msg=strategy)

    def test_rotation_from_orientation_reproduces_the_oriented_bbox(self):
        """An object with its OWN dims + the derived rotation occupies exactly the
        same world box as one built from the solver's oriented dims."""
        for result, scenario in ((SUITCASE_RESULT, SUITCASE_SCENARIO), (DRAGON_RESULT, DRAGON_SCENARIO)):
            unpacked = {o.id: o for o in scene_from_packer3d_scenario(scenario).objects}
            for strategy in ("naive", "optimized"):
                for p in result[strategy]["placements"]:
                    pos = physics_point(*p["center"])
                    own = unpacked[p["item_id"]]
                    a = obb_vertices(obb_from(Object(
                        id="a", dimensions=own.dimensions, position=pos,
                        rotation=rotation_from_orientation(p["orientation"]),
                    )))
                    b = obb_vertices(obb_from(Object(id="b", dimensions=swap_yz(p["dims"]), position=pos)))
                    np.testing.assert_allclose(a.min(axis=0), b.min(axis=0), atol=1e-12)
                    np.testing.assert_allclose(a.max(axis=0), b.max(axis=0), atol=1e-12)

    def test_placements_applied_to_the_unpacked_scene_match_the_result_scene(self):
        """The two integration flows agree: mapping the result directly, or moving the
        scanned/scenario objects with `placements_from_packer3d` + `apply_placements`."""
        for strategy in ("naive", "optimized"):
            direct, _ = scene_from_packer3d(SUITCASE_RESULT, items=SUITCASE_SCENARIO, strategy=strategy)
            moved = apply_placements(
                scene_from_packer3d_scenario(SUITCASE_SCENARIO),
                placements_from_packer3d(SUITCASE_RESULT, strategy=strategy),
            )
            by_id = {o.id: o for o in moved.objects}
            for o in direct.objects:
                a = obb_vertices(obb_from(o))
                b = obb_vertices(obb_from(by_id[o.id]))
                np.testing.assert_allclose(a.min(axis=0), b.min(axis=0), atol=1e-12)
                np.testing.assert_allclose(a.max(axis=0), b.max(axis=0), atol=1e-12)


class SuitcaseAgreementTests(unittest.TestCase):
    def test_every_placed_item_is_contained_and_collision_free(self):
        for strategy in ("naive", "optimized"):
            scene, _ = scene_from_packer3d(SUITCASE_RESULT, items=SUITCASE_SCENARIO, strategy=strategy)
            self.assertEqual(check_scene_containment(scene), [], f"{strategy}: containment violations")
            result = validate_packer3d(SUITCASE_RESULT, items=SUITCASE_SCENARIO, strategy=strategy)
            collisions = [v for v in result["violations"] if v["type"] == "OBJECT_COLLISION"]
            self.assertEqual(collisions, [], f"{strategy}: packer3d.verify() reports no overlap either")

    def test_both_suitcase_strategies_validate_clean(self):
        """Recorded agreement (docs/SOLVER_INTEGRATION.md): our stricter checker finds
        nothing on either suitcase layout -- no violations AND no warnings."""
        for strategy in ("naive", "optimized"):
            result = validate_packer3d(SUITCASE_RESULT, items=SUITCASE_SCENARIO, strategy=strategy)
            self.assertTrue(result["valid"], (strategy, result["violations"]))
            self.assertEqual(result["warnings"], [], strategy)

    def test_extras_carry_unpacked_cylinders_and_metrics(self):
        scene, extras = scene_from_packer3d(SUITCASE_RESULT, items=SUITCASE_SCENARIO, strategy="optimized")
        self.assertEqual(extras["strategy"], "optimized")
        self.assertEqual(extras["metrics"], SUITCASE_RESULT["optimized"]["metrics"])
        # unpacked items are NOT in the scene, they come back separately
        unpacked_ids = [u["id"] for u in extras["unpacked"]]
        self.assertIn("gifts", unpacked_ids)
        scene_ids = {o.id for o in scene.objects}
        self.assertTrue(scene_ids.isdisjoint(unpacked_ids))
        # cylinders are mapped as their bbox, but the cylinder fields survive
        self.assertEqual(
            extras["shapes"]["water_bottle"],
            {"shape": "cylinder", "radius": 0.04, "height": 0.26, "axis": "z"},
        )
        self.assertEqual(scene_ids & set(extras["shapes"]), set(extras["shapes"]))

    def test_keep_upright_comes_from_the_scenario(self):
        scene, _ = scene_from_packer3d(SUITCASE_RESULT, items=SUITCASE_SCENARIO, strategy="optimized")
        by_id = {o.id: o for o in scene.objects}
        self.assertTrue(by_id["camera"].constraints.keep_upright)
        self.assertIsNone(by_id["camera"].constraints.orientation_lock)
        self.assertFalse(by_id["shoes_1"].constraints.keep_upright)
        # fragile == "nothing may rest on it"
        self.assertTrue(by_id["camera"].constraints.fragile)
        self.assertTrue(by_id["camera"].constraints.cannot_support_weight)
        # without items= we simply don't know about keep_upright
        plain, _ = scene_from_packer3d(SUITCASE_RESULT, strategy="optimized")
        self.assertFalse({o.id: o for o in plain.objects}["camera"].constraints.keep_upright)

    def test_compare_wrapper_needs_a_strategy(self):
        with self.assertRaises(ValueError) as ctx:
            scene_from_packer3d(SUITCASE_RESULT)
        self.assertIn("naive", str(ctx.exception))
        with self.assertRaises(ValueError):
            scene_from_packer3d(SUITCASE_RESULT, strategy="nonesuch")
        # a single-strategy result needs no strategy=
        single, _ = scene_from_packer3d(SUITCASE_RESULT["naive"])
        self.assertEqual(len(single.objects), len(SUITCASE_RESULT["naive"]["placements"]))


class ObstacleTests(unittest.TestCase):
    def test_obstacles_become_massless_objects(self):
        scene, _ = scene_from_packer3d(DRAGON_RESULT, items=DRAGON_SCENARIO, strategy="naive")
        obstacles = [o for o in scene.objects if o.id.startswith("obstacle:")]
        self.assertEqual([o.id for o in obstacles], ["obstacle:hatch"])
        hatch = obstacles[0]
        self.assertEqual(hatch.mass_kg, 0.0)
        self.assertEqual(hatch.rigidity, "rigid")
        self.assertEqual(hatch.dimensions, (0.5, 0.2, 0.5))
        self.assertEqual(hatch.position, (1.6, 1.05, -1.6))

        without, _ = scene_from_packer3d(DRAGON_RESULT, strategy="naive", include_obstacles=False)
        self.assertEqual([o for o in without.objects if o.id.startswith("obstacle:")], [])

    def test_an_item_placed_into_the_obstacle_collides_with_it(self):
        fake = json.loads(json.dumps(DRAGON_RESULT["naive"]))
        # drop a box right on top of the hatch (position [1.35, 1.35, 0.95], dims 0.5^2 x 0.2)
        fake["placements"].append({
            "item_id": "intruder", "shape": "box",
            "position": [1.4, 1.4, 1.0], "dims": [0.2, 0.2, 0.1], "center": [1.5, 1.5, 1.05],
            "orientation": "xyz", "mass": 1.0, "fragile": False,
        })
        result = validate_packer3d(fake)
        pairs = [v["objects"] for v in result["violations"] if v["type"] == "OBJECT_COLLISION"]
        self.assertIn(["intruder", "obstacle:hatch"], pairs)

    def test_zero_total_mass_scene_has_finite_metrics(self):
        """Obstacles are massless; a scene of only obstacles must not divide by zero."""
        scene, _ = scene_from_packer3d(DRAGON_RESULT, strategy="naive")
        obstacles = [o for o in scene.objects if o.id.startswith("obstacle:")]
        metrics = scene_metrics(precompute(Scene(container=scene.container, objects=obstacles)))
        self.assertEqual(metrics["total_mass_kg"], 0.0)
        self.assertTrue(all(np.isfinite(metrics["center_of_mass"])))
        self.assertTrue(all(np.isfinite(metrics["com_offset_m"])))
        self.assertTrue(np.isfinite(metrics["fill_ratio"]))
        # COM falls back to the container centre
        np.testing.assert_allclose(metrics["center_of_mass"], scene.container.position, atol=1e-12)


class ScenarioSceneTests(unittest.TestCase):
    def test_every_expanded_item_is_outside_the_container_on_the_floor(self):
        scene = scene_from_packer3d_scenario(SUITCASE_SCENARIO)
        L, W, H = SUITCASE_SCENARIO["container"]["dims"]
        ids = [o.id for o in scene.objects]
        self.assertEqual(len(ids), 18)  # count: 2 expands shoes/jeans/shirts/books
        self.assertEqual(len(set(ids)), 18)
        for expanded in ("shoes_1", "shoes_2", "books_1", "books_2"):
            self.assertIn(expanded, ids)
        self.assertNotIn("shoes", ids)

        for o in scene.objects:
            self.assertGreater(o.position[0], L, o.id)  # outside, along +X
            v = obb_vertices(obb_from(o))
            self.assertGreaterEqual(float(v[:, 0].min()), L + TABLE_GAP_M - 1e-12, o.id)
            self.assertAlmostEqual(float(v[:, 1].min()), 0.0, delta=1e-12)  # on the floor plane
            self.assertAlmostEqual(o.position[2], -W / 2.0, delta=1e-12)
            self.assertEqual(o.rotation, IDENTITY_ROTATION)

    def test_dims_and_constraints_come_from_the_scenario(self):
        by_id = {o.id: o for o in scene_from_packer3d_scenario(SUITCASE_SCENARIO).objects}
        self.assertEqual(by_id["shoes_1"].dimensions, (0.32, 0.12, 0.2))  # (length, height, depth)
        self.assertEqual(by_id["shoes_1"].mass_kg, 1.2)
        self.assertTrue(by_id["laptop"].constraints.cannot_support_weight)
        self.assertTrue(by_id["camera"].constraints.keep_upright)
        # cylinders are their bounding box: (2r, h, 2r)
        self.assertEqual(by_id["water_bottle"].dimensions, (0.08, 0.26, 0.08))

    def test_items_are_laid_out_in_a_non_overlapping_row(self):
        scene = scene_from_packer3d_scenario(SUITCASE_SCENARIO)
        xs = [(obb_vertices(obb_from(o))[:, 0].min(), obb_vertices(obb_from(o))[:, 0].max()) for o in scene.objects]
        for (_, hi), (lo, _) in zip(xs, xs[1:]):
            self.assertGreater(lo, hi)


class CliTests(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "physics", *args], cwd=REPO_ROOT, capture_output=True, text=True
        )

    def test_valid_result_exits_0(self):
        proc = self._run(
            "validate-packer3d", "packer3d/examples/suitcase_result.json",
            "--strategy", "optimized", "--items", "packer3d/examples/suitcase.json", "--pretty",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        parsed = json.loads(proc.stdout)
        self.assertTrue(parsed["valid"])
        self.assertEqual(parsed["adapter"]["strategy"], "optimized")

    def test_violations_exit_1(self):
        # dragon: microgravity + a bolted-down hatch obstacle -> UNSUPPORTED_OBJECT
        proc = self._run("validate-packer3d", "packer3d/examples/dragon_result.json", "--strategy", "naive")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        parsed = json.loads(proc.stdout)
        self.assertFalse(parsed["valid"])

    def test_bad_input_exits_2(self):
        self.assertEqual(self._run("validate-packer3d", "does_not_exist.json").returncode, 2)
        # a --compare result without --strategy is a usage error, not a verdict
        proc = self._run("validate-packer3d", "packer3d/examples/suitcase_result.json")
        self.assertEqual(proc.returncode, 2, proc.stdout)


if __name__ == "__main__":
    unittest.main()
