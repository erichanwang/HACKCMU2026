"""Tests for pan.demo (the end-to-end demo pipeline) and pan/__main__.py's CLI.

Everything here runs offline with backend="mock" -- no network, no real PAN
access. Assertions are on structure/statuses (PAN.md sec 20 contract), never
on pixel values or on the mock backend's internal metadata["mode"].
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from pan.demo import build_demo_state, run_demo
from pan.rollouts import RolloutBatch, packing_subset
from pan.types import PackingAction, RiskSignals, RolloutRecord, SimulationResult, apply_action, honesty_note
from physics.validator import validate_layout

_REPO_ROOT = Path(__file__).resolve().parent.parent
_IDENTITY = (0.0, 0.0, 0.0, 1.0)


# --------------------------------------------------------------------- run_demo
class RunDemoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out_dir = Path(self._tmp.name) / "demo_out"

    def test_run_demo_end_to_end(self):
        t0 = time.monotonic()
        payload = run_demo(self.out_dir, backend="mock", steps=2, num_frames=4, viewpoint="overhead_45", timeout_s=30)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 60.0, "run_demo(backend='mock') should be fast and never block on real PAN")

        json_path = self.out_dir / "candidates.json"
        self.assertTrue(json_path.exists())
        on_disk = json.loads(json_path.read_text())
        self.assertEqual(on_disk, payload)

        candidates = {c["candidate_id"]: c for c in payload["candidates"]}
        self.assertEqual(set(candidates), {"A", "B", "C"})

        self.assertEqual(candidates["A"]["physics_status"], "valid")
        self.assertEqual(candidates["B"]["physics_status"], "valid")
        self.assertEqual(candidates["A"]["simulation_status"], "complete")
        self.assertEqual(candidates["B"]["simulation_status"], "complete")
        self.assertEqual(candidates["C"]["simulation_status"], "unavailable")

        for cid in ("A", "B", "C"):
            sc = candidates[cid]["score_components"]
            for key in ("geometry_score", "stability_score", "pan_risk", "execution_risk_weight", "total", "n_steps"):
                self.assertIn(key, sc, f"missing score component {key!r} for candidate {cid}")

        for cid in ("A", "B"):
            video_rel = candidates[cid]["pan_preview_video"]
            frame_rel = candidates[cid]["pan_final_frame"]
            self.assertIsNotNone(video_rel)
            self.assertIsNotNone(frame_rel)
            video_path = self.out_dir / video_rel
            frame_path = self.out_dir / frame_rel
            self.assertTrue(video_path.exists(), video_path)
            self.assertTrue(frame_path.exists(), frame_path)
            with Image.open(video_path) as im:
                self.assertEqual(getattr(im, "n_frames", 1), 4)

            for step_dir in sorted((self.out_dir / "candidates" / cid).glob("step_*")):
                self.assertTrue((step_dir / "expected.png").exists())

        self.assertTrue((self.out_dir / "comparison.png").exists())
        self.assertTrue((self.out_dir / "state_observation.png").exists())

        summary = (self.out_dir / "summary.txt").read_text()
        self.assertIn("Candidate A", summary)

    def test_every_displayed_rollout_is_labelled_with_its_backend(self):
        """Nothing may present a mock rollout as a PAN prediction: the payload,
        summary.txt and each persisted asset dir all name the backend + note."""
        payload = run_demo(self.out_dir, backend="mock", steps=1, num_frames=3, timeout_s=30)
        note = "synthetic frames drawn by the mock, not a world-model prediction"

        self.assertEqual(payload["backend"], "cache(mock)")
        self.assertEqual(payload["backend_note"], note)

        candidates = {c["candidate_id"]: c for c in payload["candidates"]}
        for cid in ("A", "B"):  # the two that actually produced a rollout
            self.assertEqual(candidates[cid]["backend"], "mock", cid)
            self.assertEqual(candidates[cid]["backend_note"], note, cid)
            step_dir = self.out_dir / "candidates" / cid / "step_0"
            self.assertEqual((step_dir / "backend.txt").read_text(), f"world model: mock -- {note}\n")
        # C was gated by physics: no rollout, so no backend to label
        self.assertIsNone(candidates["C"]["backend"])
        for step in payload["steps"]:
            if step["simulation_status"] == "complete":
                self.assertEqual(step["backend_note"], note, step["candidate_id"])

        summary = (self.out_dir / "summary.txt").read_text()
        self.assertEqual(summary.splitlines()[0], f"world model: cache(mock) -- {note}")
        self.assertIn(f"  world model: mock -- {note}", summary)

    def test_candidate_c_first_step_is_physics_invalid_fragile_overloaded(self):
        state, candidates, _labels = build_demo_state()
        cand_c = next(c for c in candidates if c.candidate_id == "C")
        after = apply_action(state, cand_c.actions[0])
        gate_scene = packing_subset(after)
        result = validate_layout(gate_scene)
        self.assertFalse(result["valid"])
        laptop_violation_types = [v["type"] for v in result["violations"] if v.get("object") == "laptop"]
        self.assertIn("FRAGILE_OBJECT_OVERLOADED", laptop_violation_types)


# ------------------------------------------------------- candidate_reports() rollup
def _physics_dict(valid: bool, malformed: bool = False) -> dict:
    if malformed:
        return {"valid": False, "score": 0.0, "violations": [{"type": "MALFORMED_GEOMETRY", "object": "x"}], "warnings": []}
    if valid:
        return {"valid": True, "score": 1.0, "violations": [], "warnings": []}
    return {"valid": False, "score": 0.0, "violations": [{"type": "OBJECT_COLLISION", "objects": ["a", "b"]}], "warnings": []}


def _action(candidate_id: str, order_index: int) -> PackingAction:
    return PackingAction(
        object_id=f"{candidate_id}_obj",
        target_position=(0.0, 0.0, 0.0),
        target_rotation=_IDENTITY,
        order_index=order_index,
        text=f"step {order_index} of {candidate_id}",
    )


def _rec(candidate_id, order_index, *, physics_valid=True, malformed=False, status="complete", result=None, risk=None, error=None) -> RolloutRecord:
    return RolloutRecord(
        candidate_id=candidate_id,
        action=_action(candidate_id, order_index),
        physics=_physics_dict(physics_valid, malformed),
        physics_valid=physics_valid and not malformed,
        status=status,
        result=result,
        risk=risk,
        error=error,
    )


def _sim_result(video: str, frame: str) -> SimulationResult:
    return SimulationResult(
        request_id="r",
        status="complete",
        backend="mock",
        frames=[np.zeros((4, 4, 3), dtype=np.uint8)],
        video_path=video,
        final_frame_path=frame,
    )


def _risk(level_hint: float) -> RiskSignals:
    return RiskSignals(
        accessibility_risk=level_hint,
        visible_shift=False,
        possible_topple=False,
        occlusion_risk=0.0,
        confidence=0.9,
    )


class CandidateReportsRollupTests(unittest.TestCase):
    def test_malformed_beats_invalid_and_failed_beats_pending_and_asset_carries_forward(self):
        rec0 = _rec("P", 0, physics_valid=True, status="complete", result=_sim_result("v0.gif", "f0.png"), risk=_risk(0.1))
        rec1 = _rec("P", 1, malformed=True, status="failed", error="boom")
        batch = RolloutBatch([rec0, rec1], [0, 1], [7, 7], {"P": "p label"})
        rollup = batch.candidate_reports()[0]
        self.assertEqual(rollup.physics_status, "malformed")
        self.assertEqual(rollup.simulation_status, "failed")
        self.assertEqual(rollup.execution_risk, "low")  # only step0 has a level
        self.assertEqual(rollup.pan_preview_video, "v0.gif")  # step1 has no assets, doesn't blank it out
        self.assertEqual(rollup.pan_final_frame, "f0.png")
        self.assertIn(" Then: ", rollup.action_text)
        self.assertEqual(rollup.score_components["n_steps"], 2)
        self.assertEqual(len(rollup.risk_metadata["steps"]), 2)

    def test_pending_beats_unavailable_when_nothing_failed(self):
        rec0 = _rec("Q", 0, physics_valid=True, status="unavailable")
        rec1 = _rec("Q", 1, physics_valid=True, status="pending")
        batch = RolloutBatch([rec0, rec1], [0, 1], [7, 7], {"Q": "q label"})
        rollup = batch.candidate_reports()[0]
        self.assertEqual(rollup.physics_status, "valid")
        self.assertEqual(rollup.simulation_status, "pending")

    def test_worst_risk_level_and_latest_step_asset_wins(self):
        rec0 = _rec("R", 0, physics_valid=True, status="complete", result=_sim_result("v0.gif", "f0.png"), risk=_risk(0.1))
        rec1 = _rec("R", 1, physics_valid=True, status="complete", result=_sim_result("v1.gif", "f1.png"), risk=_risk(0.9))
        batch = RolloutBatch([rec0, rec1], [0, 1], [7, 7], {"R": "r label"})
        rollup = batch.candidate_reports()[0]
        self.assertEqual(rollup.execution_risk, "high")  # low + high -> high
        self.assertEqual(rollup.pan_preview_video, "v1.gif")
        self.assertEqual(rollup.pan_final_frame, "f1.png")

    def test_all_valid_all_complete_no_risk_gives_none_execution_risk(self):
        rec0 = _rec("S", 0, physics_valid=True, status="complete", result=_sim_result("v0.gif", "f0.png"))
        batch = RolloutBatch([rec0], [0], [7], {"S": "s label"})
        rollup = batch.candidate_reports()[0]
        self.assertEqual(rollup.physics_status, "valid")
        self.assertEqual(rollup.simulation_status, "complete")
        self.assertIsNone(rollup.execution_risk)


# ------------------------------------------------------------------ honesty_note
class HonestyNoteTests(unittest.TestCase):
    def test_note_names_what_each_backend_is_and_keeps_the_backends_own_note(self):
        self.assertIn("not a world-model prediction", honesty_note("mock"))
        self.assertIn("not a world-model prediction", honesty_note("cache(mock)"))
        self.assertIn("unverified", honesty_note("pan"))
        # a backend that labels itself keeps that label, appended -- never instead
        line = honesty_note("mock", {"mode": "static", "note": "held the scene still"})
        self.assertIn("not a world-model prediction", line)
        self.assertIn("held the scene still", line)

    def test_unknown_backend_is_flagged_rather_than_silently_blank(self):
        self.assertIn("unlabelled", honesty_note("something-new"))


# --------------------------------------------------------------------------- CLI
class CliTests(unittest.TestCase):
    def test_status_never_leaks_env_var_values(self):
        env = dict(os.environ)
        secret = "super-secret-value-should-never-leak"
        env["PAN_API_KEY"] = secret
        proc = subprocess.run(
            [sys.executable, "-m", "pan", "status"],
            capture_output=True, text=True, cwd=str(_REPO_ROOT), env=env, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn(secret, proc.stdout)
        self.assertNotIn(secret, proc.stderr)
        self.assertIn("PAN_API_KEY", proc.stdout)  # the NAME is fine to mention

    def test_demo_cli_exits_zero_and_writes_candidates_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cli_out"
            proc = subprocess.run(
                [sys.executable, "-m", "pan", "demo", "--backend", "mock", "--frames", "3", "--steps", "1", "--out", str(out)],
                capture_output=True, text=True, cwd=str(_REPO_ROOT), timeout=90,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue((out / "candidates.json").exists())
            self.assertIn("Candidate A", proc.stdout)


if __name__ == "__main__":
    unittest.main()
