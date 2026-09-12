"""Tests for `pan.solver_bridge` and the solver path through `pan.demo.run_demo`.

Offline: backend="mock", no network, no solver run -- only the checked-in
`packer3d/examples/*.json`.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pan.demo import build_solver_state, run_demo
from pan.solver_bridge import candidates_from_packer3d, first_divergence, humanize
from pan.types import CandidateSequence, PackingAction
from physics.packer3d_adapter import physics_point

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULT_PATH = REPO_ROOT / "packer3d" / "examples" / "suitcase_result.json"
SCENARIO_PATH = REPO_ROOT / "packer3d" / "examples" / "suitcase.json"
RESULT = json.loads(RESULT_PATH.read_text())
SCENARIO = json.loads(SCENARIO_PATH.read_text())


def _action(object_id: str, i: int = 0) -> PackingAction:
    return PackingAction(object_id=object_id, target_position=(0.0, 0.0, 0.0), order_index=i)


class CandidatesTests(unittest.TestCase):
    def test_one_candidate_per_strategy_with_every_placement_in_solver_order(self):
        scene, candidates, labels = candidates_from_packer3d(RESULT, SCENARIO)
        self.assertEqual([c.candidate_id for c in candidates], ["naive", "optimized"])
        self.assertEqual([c.label for c in candidates], ["naive first-fit order", "optimized order"])

        scene_ids = {o.id for o in scene.objects}
        for cand in candidates:
            placements = RESULT[cand.candidate_id]["placements"]
            self.assertEqual(len(cand.actions), len(placements), cand.candidate_id)
            for i, (action, p) in enumerate(zip(cand.actions, placements)):
                self.assertEqual(action.object_id, p["item_id"])
                self.assertEqual(action.order_index, i)
                self.assertEqual(action.candidate_id, cand.candidate_id)
                self.assertIn(action.object_id, scene_ids)  # the state holds every item
                for k in range(3):
                    self.assertAlmostEqual(action.target_position[k], physics_point(*p["center"])[k], delta=1e-12)

        self.assertEqual(labels["shoes_1"], "shoes 1")
        self.assertEqual(labels["camera"], "camera")
        self.assertEqual(set(labels), scene_ids)

    def test_labels_can_be_overridden(self):
        _, candidates, labels = candidates_from_packer3d(RESULT, SCENARIO, labels={"shoes_1": "black running shoe"})
        self.assertEqual(labels["shoes_1"], "black running shoe")
        self.assertEqual(labels["shoes_2"], "shoes 2")
        naive = next(c for c in candidates if c.candidate_id == "naive")
        self.assertEqual(naive.actions[0].label, "black running shoe")

    def test_single_strategy_result_gives_one_candidate(self):
        _, candidates, _ = candidates_from_packer3d(RESULT["optimized"], SCENARIO)
        self.assertEqual([c.candidate_id for c in candidates], ["optimized"])
        self.assertIsNone(first_divergence(candidates))

    def test_first_divergence_is_a_valid_index(self):
        _, candidates, _ = candidates_from_packer3d(RESULT, SCENARIO)
        k = first_divergence(candidates)
        self.assertIsNotNone(k)
        self.assertTrue(0 <= k < min(len(c.actions) for c in candidates))
        acted = {c.actions[k].object_id for c in candidates}
        self.assertEqual(len(acted), len(candidates), "the divergent step must act on different objects")
        for i in range(k):
            self.assertEqual(len({c.actions[i].object_id for c in candidates}), 1)

    def test_first_divergence_edge_cases(self):
        a = CandidateSequence("a", [_action("x", 0), _action("y", 1)])
        b = CandidateSequence("b", [_action("x", 0), _action("y", 1)])
        self.assertIsNone(first_divergence([a, b]))
        self.assertIsNone(first_divergence([a]))
        short = CandidateSequence("c", [_action("x", 0)])
        self.assertEqual(first_divergence([a, short]), 1)  # prefix equal, lengths differ
        other = CandidateSequence("d", [_action("x", 0), _action("z", 1)])
        self.assertEqual(first_divergence([a, other]), 1)

    def test_humanize(self):
        self.assertEqual(humanize("shoes_1"), "shoes 1")
        self.assertEqual(humanize("water_bottle"), "water bottle")


class SolverStateTests(unittest.TestCase):
    def test_state_is_rolled_forward_to_the_divergence(self):
        _, raw, _ = candidates_from_packer3d(RESULT, SCENARIO)
        k = first_divergence(raw) or 0
        state, candidates, labels, note = build_solver_state(RESULT_PATH, SCENARIO_PATH)
        self.assertEqual([c.candidate_id for c in candidates], ["naive", "optimized"])
        for c, r in zip(candidates, raw):
            self.assertEqual(len(c.actions), len(r.actions) - k)
            self.assertEqual([a.order_index for a in c.actions], list(range(len(c.actions))))
        self.assertIn(f"imagining step {k + 1}", note)
        for c in candidates:
            self.assertIn(f"{c.candidate_id} places {c.actions[0].object_id}", note)

    def test_strategy_selection(self):
        _, candidates, _, _ = build_solver_state(RESULT_PATH, SCENARIO_PATH, strategy_a="optimized", strategy_b="")
        self.assertEqual([c.candidate_id for c in candidates], ["optimized"])


class RunDemoSolverTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out_dir = Path(self._tmp.name) / "solver_demo"

    def test_run_demo_from_packer3d(self):
        payload = run_demo(
            self.out_dir, backend="mock", steps=1, num_frames=3,
            from_packer3d=RESULT_PATH, scenario=SCENARIO_PATH, timeout_s=60,
        )
        self.assertEqual(json.loads((self.out_dir / "candidates.json").read_text()), payload)

        candidates = {c["candidate_id"]: c for c in payload["candidates"]}
        self.assertEqual(set(candidates), {"naive", "optimized"})
        for cid, report in candidates.items():
            # Recorded agreement: the solver's first divergent placement is physics-valid
            # for BOTH strategies (docs/SOLVER_INTEGRATION.md).
            self.assertEqual(report["physics_status"], "valid", cid)
            self.assertEqual(report["simulation_status"], "complete", cid)

        summary = (self.out_dir / "summary.txt").read_text()
        self.assertIn("imagining step", summary)
        self.assertEqual(summary.splitlines()[0], payload["divergence"])
        self.assertTrue((self.out_dir / "state_observation.png").exists())

    def test_scenario_is_required(self):
        with self.assertRaises(ValueError):
            run_demo(self.out_dir, backend="mock", from_packer3d=RESULT_PATH)

    def test_cli_rejects_from_packer3d_without_scenario(self):
        proc = subprocess.run(
            [sys.executable, "-m", "pan", "demo", "--from-packer3d", str(RESULT_PATH), "--out", str(self.out_dir)],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--scenario", proc.stderr)


if __name__ == "__main__":
    unittest.main()
