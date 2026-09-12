"""Seeded fuzz tests for the physics validator (`validate_layout`) and the
incremental placer (`physics.incremental.PlacementValidator`).

Run: python3 -m unittest tests.test_fuzz_validator -v

All ~200 scenes here are axis-aligned (identity rotation, `random.Random(0)`
-seeded, fully deterministic) so a hand-computed ground truth (plain AABB
interval overlap / wall containment / floor-and-object support -- no SAT or
convex-hull code reused from `physics/`) can be checked against
`validate_layout`'s actual output. Every object is rigid with default
constraints, so CONTAINER_PENETRATION / OBJECT_COLLISION / UNSUPPORTED_OBJECT
are the only violation types that can fire, and the ground truth model
covers exactly those three.

Two scene families:
  (a) `valid_stack_scene` -- a single bottom-up column, each box's XZ
      footprint fully inside the one below's, flush and inside the
      container. MUST validate clean (no violations, no warnings).
  (b) `random_scene` -- each box independently placed on the floor
      (sometimes overhanging a wall), stacked on an earlier box, floating,
      or deliberately overlapping an earlier box. Ground truth violations
      are compared to the validator's by (type, object-id-or-pair).
"""
from __future__ import annotations

import itertools
import json
import random
import unittest

from physics.incremental import PlacementValidator
from physics.io import result_to_json
from physics.schema import Container, Object, Scene
from physics.validator import validate_layout

RNG_SEED = 0
NUM_VALID_STACK_SCENES = 100
NUM_RANDOM_SCENES = 100
# Incremental fuzzing does ~2x the validate_layout calls per scene (full
# pipeline twice, plus PlacementValidator's own O(k) rebuild per commit) --
# a smaller trial count keeps total CPU well under the shared budget.
NUM_INCREMENTAL_SCENES = 40
EPS = 1e-6  # matches collision.py / containment.py default epsilon
CONTACT_EPS = 1e-3  # matches support.py's resting_pairs/on_floor epsilon
FLOATING_THRESHOLD = 0.05  # matches validate_layout's default


def _rand_dim(rng: random.Random) -> float:
    return rng.uniform(0.01, 0.30)  # 1-30 cm


def _container(cx: float, cy: float, cz: float) -> Container:
    # Floor at world y=0 for every scene in this file (position = half-Y up).
    return Container(id="container", dimensions=(cx, cy, cz), position=(0.0, cy / 2.0, 0.0))


def _obj(oid: str, dims: tuple[float, float, float], position: tuple[float, float, float]) -> Object:
    return Object(id=oid, dimensions=dims, position=position)


# ---------------------------------------------------------------------------
# (a) valid stacking scenes -- must always validate clean
# ---------------------------------------------------------------------------


def valid_stack_scene(rng: random.Random) -> Scene:
    """A single vertical column of 1-8 boxes, each centered at x=z=0, flush
    on top of the one below (or the floor), with XZ footprint non-increasing
    going up the stack -- so each box's footprint is fully inside the one
    below's, guaranteeing support_ratio == 1.0 and a centered (hence
    stable) support polygon. The container is sized with a comfortable
    margin around the resulting column, so containment always holds too.
    """
    n = rng.randint(1, 8)
    dims: list[tuple[float, float, float]] = []
    x, z = _rand_dim(rng), _rand_dim(rng)
    for i in range(n):
        y = _rand_dim(rng)
        if i > 0:
            x = rng.uniform(0.01, x)
            z = rng.uniform(0.01, z)
        dims.append((x, y, z))

    objects = []
    top = 0.0
    for i, (dx, dy, dz) in enumerate(dims):
        objects.append(_obj(f"box{i}", (dx, dy, dz), (0.0, top + dy / 2.0, 0.0)))
        top += dy

    cx = dims[0][0] + 0.1
    cz = dims[0][2] + 0.1
    cy = top + 0.05
    return Scene(container=_container(cx, cy, cz), objects=objects)


# ---------------------------------------------------------------------------
# (b) random scenes -- ground truth computed independently below
# ---------------------------------------------------------------------------


def random_scene(rng: random.Random) -> Scene:
    """1-8 independently-placed axis-aligned boxes. Each is placed in one
    of four modes -- flush on the floor (sometimes overhanging a wall),
    stacked on a random earlier box, floating in mid-air, or deliberately
    overlapping a random earlier box -- so that across many seeds all of
    containment/collision/support get exercised. Ground truth is computed
    directly from the resulting AABBs regardless of mode, so any position
    is valid input to it; the mode mix only affects coverage.
    """
    cx = rng.uniform(0.1, 0.9)
    cy = rng.uniform(0.1, 0.9)
    cz = rng.uniform(0.1, 0.9)
    n = rng.randint(1, 8)

    objects: list[Object] = []
    for i in range(n):
        dx, dy, dz = _rand_dim(rng), _rand_dim(rng), _rand_dim(rng)
        mode = rng.random()
        if i == 0 or mode < 0.3:  # flush on the floor, sometimes overhanging
            x = rng.uniform(-cx * 0.65, cx * 0.65)
            z = rng.uniform(-cz * 0.65, cz * 0.65)
            y = dy / 2.0
        elif mode < 0.55:  # stacked on a random earlier box
            k = objects[rng.randrange(len(objects))]
            kx, ky, kz = k.dimensions
            x = k.position[0] + rng.uniform(-0.6, 0.6) * kx
            z = k.position[2] + rng.uniform(-0.6, 0.6) * kz
            y = k.position[1] + ky / 2.0 + dy / 2.0
        elif mode < 0.8:  # floating -- likely unsupported
            x = rng.uniform(-cx * 0.65, cx * 0.65)
            z = rng.uniform(-cz * 0.65, cz * 0.65)
            y = rng.uniform(3 * dy, 1.2 * cy)
        else:  # deliberately overlapping a random earlier box
            k = objects[rng.randrange(len(objects))]
            x = k.position[0] + rng.uniform(-0.4, 0.4) * k.dimensions[0]
            y = k.position[1] + rng.uniform(-0.4, 0.4) * k.dimensions[1]
            z = k.position[2] + rng.uniform(-0.4, 0.4) * k.dimensions[2]
        objects.append(_obj(f"box{i}", (dx, dy, dz), (x, y, z)))

    return Scene(container=_container(cx, cy, cz), objects=objects)


def _rect_overlap_area(min_a, max_a, min_b, max_b) -> float:
    ox = min(max_a[0], max_b[0]) - max(min_a[0], min_b[0])
    oz = min(max_a[1], max_b[1]) - max(min_a[1], min_b[1])
    if ox <= 0.0 or oz <= 0.0:
        return 0.0
    return ox * oz


def ground_truth_violations(scene: Scene) -> set[tuple[str, object]]:
    """Hand-computed CONTAINER_PENETRATION / OBJECT_COLLISION /
    UNSUPPORTED_OBJECT violations for an axis-aligned scene, straight from
    AABB overlap / wall containment / floor-and-object support. Every
    object here is rigid with default constraints, so these are the only
    violation types `validate_layout` can produce for it.
    """
    cont = scene.container
    chx, chy, chz = (d / 2.0 for d in cont.dimensions)
    cx0, cy0, cz0 = cont.position
    c_min = (cx0 - chx, cy0 - chy, cz0 - chz)
    c_max = (cx0 + chx, cy0 + chy, cz0 + chz)
    floor_y = c_min[1]

    mins, maxs = {}, {}
    for o in scene.objects:
        hx, hy, hz = (d / 2.0 for d in o.dimensions)
        px, py, pz = o.position
        mins[o.id] = (px - hx, py - hy, pz - hz)
        maxs[o.id] = (px + hx, py + hy, pz + hz)

    out: set[tuple[str, object]] = set()

    # containment: any wall overshoot beyond EPS
    for o in scene.objects:
        lo, hi = mins[o.id], maxs[o.id]
        depth = max(
            max(c_min[k] - lo[k] for k in range(3)),
            max(hi[k] - c_max[k] for k in range(3)),
        )
        if depth > EPS:
            out.add(("CONTAINER_PENETRATION", o.id))

    # collision: positive overlap on all 3 axes for a pair
    for a, b in itertools.combinations(scene.objects, 2):
        ov = [min(maxs[a.id][k], maxs[b.id][k]) - max(mins[a.id][k], mins[b.id][k]) for k in range(3)]
        if all(o > EPS for o in ov):
            out.add(("OBJECT_COLLISION", tuple(sorted((a.id, b.id)))))

    # support: floor + any object whose top sits within CONTACT_EPS of this
    # object's bottom, with positive XZ footprint overlap
    floor_min_xz = (c_min[0], c_min[2])
    floor_max_xz = (c_max[0], c_max[2])
    for o in scene.objects:
        lo, hi = mins[o.id], maxs[o.id]
        footprint_area = (hi[0] - lo[0]) * (hi[2] - lo[2])
        covered = 0.0
        if abs(lo[1] - floor_y) <= CONTACT_EPS:
            covered += _rect_overlap_area((lo[0], lo[2]), (hi[0], hi[2]), floor_min_xz, floor_max_xz)
        for other in scene.objects:
            if other.id == o.id:
                continue
            olo, ohi = mins[other.id], maxs[other.id]
            if abs(lo[1] - ohi[1]) <= CONTACT_EPS:
                covered += _rect_overlap_area((lo[0], lo[2]), (hi[0], hi[2]), (olo[0], olo[2]), (ohi[0], ohi[2]))
        ratio = min(1.0, covered / footprint_area)
        if ratio < FLOATING_THRESHOLD:
            out.add(("UNSUPPORTED_OBJECT", o.id))

    return out


def actual_violations(result: dict) -> set[tuple[str, object]]:
    out: set[tuple[str, object]] = set()
    for v in result["violations"]:
        if v["type"] == "OBJECT_COLLISION":
            out.add((v["type"], tuple(v["objects"])))
        elif v["type"] in ("CONTAINER_PENETRATION", "UNSUPPORTED_OBJECT"):
            out.add((v["type"], v["object"]))
    return out


def assert_common_invariants(result: dict, ids: set[str]) -> None:
    """valid iff no violations; every referenced id exists; JSON round-trip."""
    assert result["valid"] == (len(result["violations"]) == 0)
    for entry in result["violations"] + result["warnings"]:
        oid = entry.get("object")
        if oid is not None:
            assert oid in ids, f"unknown object id in violation: {oid}"
        for oid in entry.get("objects", []):
            assert oid in ids, f"unknown object id in violation: {oid}"
    reloaded = json.loads(result_to_json(result))
    assert reloaded["valid"] == result["valid"]


class TestValidStackScenes(unittest.TestCase):
    def test_clean_stack_validates(self):
        rng = random.Random(RNG_SEED)
        for trial in range(NUM_VALID_STACK_SCENES):
            scene = valid_stack_scene(rng)
            result = validate_layout(scene)
            with self.subTest(trial=trial, n=len(scene.objects)):
                self.assertEqual(result["violations"], [])
                self.assertEqual(result["warnings"], [])
                self.assertTrue(result["valid"])
                assert_common_invariants(result, {o.id for o in scene.objects})

    def test_incremental_matches_full_validate(self):
        rng = random.Random(RNG_SEED)
        for trial in range(NUM_INCREMENTAL_SCENES):
            scene = valid_stack_scene(rng)
            pv = PlacementValidator(scene.container)
            with self.subTest(trial=trial, n=len(scene.objects)):
                for obj in scene.objects:
                    step = pv.place(obj)
                    self.assertTrue(step["valid"], step)
                self.assertEqual(pv.to_scene(), scene)
                self.assertEqual(
                    result_to_json(validate_layout(pv.to_scene())),
                    result_to_json(validate_layout(scene)),
                )


class TestRandomScenes(unittest.TestCase):
    def test_ground_truth_matches_validator(self):
        rng = random.Random(RNG_SEED + 1)
        for trial in range(NUM_RANDOM_SCENES):
            scene = random_scene(rng)
            result = validate_layout(scene)
            with self.subTest(trial=trial, n=len(scene.objects)):
                expected = ground_truth_violations(scene)
                got = actual_violations(result)
                self.assertEqual(got, expected)
                assert_common_invariants(result, {o.id for o in scene.objects})


if __name__ == "__main__":
    unittest.main()
