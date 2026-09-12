"""Offline tests for pan.rollouts.RolloutManager / RolloutBatch / rank_candidates.

Everything here uses tests.fixtures.valid_packed_scene() plus a FakeWorldModel
-- no real PAN access, no network. Candidates are derived by moving shoe
(and, for one collision case, testing an invalid target) out of / around the
valid fixture scene so the physics gate has real work to do.
"""
from __future__ import annotations

import threading
import time
import unittest
from dataclasses import replace

import numpy as np

from pan.rollouts import RolloutManager, rank_candidates
from pan.types import CandidateSequence, Observation, PackingAction, RiskSignals, SimulationResult
from physics.schema import Scene
from tests.fixtures import valid_packed_scene

_IDENTITY = (0.0, 0.0, 0.0, 1.0)
_OUTSIDE = {"shoe": (5.0, 5.0, 5.0), "camera": (6.0, 5.0, 5.0)}


# --------------------------------------------------------------------- fakes
class FakeWorldModel:
    def __init__(self, delay=0.05, fail_ids=frozenset(), available=True, supports_continuation=False):
        self.name = "fake"
        self.supports_continuation = supports_continuation
        self.delay = delay
        self.fail_ids = set(fail_ids)
        self._available = available
        self.requests = []  # captured SimulationRequest objects, call order
        self.results = []  # SimulationResult objects returned, call order
        self._lock = threading.Lock()

    def available(self) -> bool:
        return self._available

    def simulate(self, request):
        with self._lock:
            self.requests.append(request)
        time.sleep(self.delay)
        if request.action.object_id in self.fail_ids:
            raise RuntimeError(f"simulated PAN failure for {request.action.object_id}")
        frame = np.zeros((16, 16, 3), dtype=np.uint8)
        result = SimulationResult(
            request_id=request.request_id,
            status="complete",
            backend="mock",
            frames=[frame, frame],
            video_path=f"/tmp/{request.request_id}.gif",
            final_frame_path=f"/tmp/{request.request_id}.png",
            latency_ms=self.delay * 1000.0,
        )
        with self._lock:
            self.results.append(result)
        return result


def fake_observation_fn(scene) -> Observation:
    return Observation(image=np.zeros((16, 16, 3), dtype=np.uint8), scene_id="test-scene", source="rendered")


def fake_describe_fn(scene, action) -> str:
    return f"move {action.object_id}"


def fake_evaluate_fn(result, observation, action):
    return RiskSignals(
        accessibility_risk=0.1,
        visible_shift=False,
        possible_topple=False,
        occlusion_risk=0.0,
        confidence=0.9,
    )


# ------------------------------------------------------------------- helpers
def _fixture_pose(object_id: str):
    for o in valid_packed_scene().objects:
        if o.id == object_id:
            return o.position, o.rotation
    raise KeyError(object_id)


def _scene_with_out(*object_ids: str) -> Scene:
    scene = valid_packed_scene()
    objects = [
        replace(o, position=_OUTSIDE[o.id]) if o.id in object_ids else o for o in scene.objects
    ]
    return Scene(container=scene.container, objects=objects)


def _action(object_id, position, rotation=_IDENTITY, order_index=0) -> PackingAction:
    return PackingAction(
        object_id=object_id, target_position=position, target_rotation=rotation, order_index=order_index
    )


def _fixture_action(object_id, order_index=0) -> PackingAction:
    pos, rot = _fixture_pose(object_id)
    return _action(object_id, pos, rot, order_index)


def _valid_candidate(cid="shoe_first") -> CandidateSequence:
    return CandidateSequence(candidate_id=cid, actions=[_fixture_action("shoe")], label="shoe back")


def _collision_candidate(cid="collision") -> CandidateSequence:
    laptop_pos, laptop_rot = _fixture_pose("laptop")
    return CandidateSequence(
        candidate_id=cid, actions=[_action("shoe", laptop_pos, laptop_rot)], label="shoe into laptop"
    )


def _two_step_candidate(cid="two_step") -> CandidateSequence:
    charger_pos, charger_rot = _fixture_pose("charger")
    return CandidateSequence(
        candidate_id=cid,
        actions=[_fixture_action("shoe", order_index=0), _action("charger", charger_pos, charger_rot, order_index=1)],
        label="shoe then charger (noop)",
    )


class RolloutManagerTests(unittest.TestCase):
    def test_returns_immediately_with_pending_records(self):
        scene = _scene_with_out("shoe")
        wm = FakeWorldModel(delay=0.2)
        mgr = RolloutManager(wm, observation_fn=fake_observation_fn, describe_fn=fake_describe_fn)
        t0 = time.monotonic()
        batch = mgr.simulate_candidate_actions(scene, [_valid_candidate()], steps=1)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, wm.delay)
        self.assertEqual(batch.records[0].status, "pending")
        self.assertTrue(batch.wait(timeout=5))
        mgr.shutdown(wait=True)

    def test_wait_completes_with_risk_and_logs(self):
        scene = _scene_with_out("shoe")
        wm = FakeWorldModel(delay=0.02)
        mgr = RolloutManager(
            wm, observation_fn=fake_observation_fn, describe_fn=fake_describe_fn, evaluate_fn=fake_evaluate_fn
        )
        with self.assertLogs("pan.rollouts", level="INFO") as cm:
            batch = mgr.simulate_candidate_actions(scene, [_valid_candidate()], steps=1)
            self.assertTrue(batch.wait(timeout=5))
        rec = batch.records[0]
        self.assertEqual(rec.status, "complete")
        self.assertTrue(rec.physics_valid)
        self.assertIsNotNone(rec.risk)
        self.assertAlmostEqual(rec.risk.accessibility_risk, 0.1)
        self.assertTrue(any("shoe_first" in line and "latency_ms" in line for line in cm.output))
        mgr.shutdown(wait=True)

    def test_colliding_candidate_is_gated_and_never_reaches_backend(self):
        scene = _scene_with_out("shoe")
        wm = FakeWorldModel(delay=0.02)
        mgr = RolloutManager(wm, observation_fn=fake_observation_fn, describe_fn=fake_describe_fn)
        batch = mgr.simulate_candidate_actions(scene, [_collision_candidate()], steps=1)
        rec = batch.records[0]
        self.assertTrue(batch.done())  # resolved synchronously, no PAN call needed
        self.assertEqual(rec.status, "unavailable")
        self.assertFalse(rec.physics_valid)
        self.assertEqual(rec.error, "skipped: physics invalid")
        self.assertEqual(wm.requests, [])
        mgr.shutdown(wait=True)

    def test_failing_backend_id_marks_record_failed(self):
        scene = _scene_with_out("shoe")
        wm = FakeWorldModel(delay=0.01, fail_ids={"shoe"})
        mgr = RolloutManager(wm, observation_fn=fake_observation_fn, describe_fn=fake_describe_fn)
        batch = mgr.simulate_candidate_actions(scene, [_valid_candidate()], steps=1)
        self.assertTrue(batch.wait(timeout=5))
        rec = batch.records[0]
        self.assertEqual(rec.status, "failed")
        self.assertIn("simulated PAN failure", rec.error)
        mgr.shutdown(wait=True)

    def test_unavailable_world_model_is_instant_and_spawns_no_threads(self):
        scene = _scene_with_out("shoe")
        wm = FakeWorldModel(delay=5.0, available=False)  # would time out the test if ever awaited
        mgr = RolloutManager(wm, observation_fn=fake_observation_fn, describe_fn=fake_describe_fn)
        t0 = time.monotonic()
        batch = mgr.simulate_candidate_actions(scene, [_valid_candidate()], steps=1)
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 1.0)
        self.assertTrue(batch.done())
        rec = batch.records[0]
        self.assertEqual(rec.status, "unavailable")
        self.assertEqual(rec.error, "world model unavailable")
        self.assertEqual(wm.requests, [])
        mgr.shutdown(wait=True)

    def test_cancel_marks_remaining_failed_cancelled(self):
        scene = _scene_with_out("shoe")
        wm = FakeWorldModel(delay=0.3)
        mgr = RolloutManager(wm, max_workers=2, observation_fn=fake_observation_fn, describe_fn=fake_describe_fn)
        candidates = [_valid_candidate(cid=f"shoe_{i}") for i in range(4)]
        batch = mgr.simulate_candidate_actions(scene, candidates, steps=1)
        cancelled = batch.cancel()
        self.assertEqual(cancelled, 4)
        self.assertTrue(batch.wait(timeout=5))
        for rec in batch.records:
            self.assertEqual(rec.status, "failed")
            self.assertEqual(rec.error, "cancelled")
        mgr.shutdown(wait=True)

    def test_two_step_candidate_chains_and_passes_continuation_history(self):
        scene = _scene_with_out("shoe")
        wm = FakeWorldModel(delay=0.01, supports_continuation=True)
        mgr = RolloutManager(wm, observation_fn=fake_observation_fn, describe_fn=fake_describe_fn)
        batch = mgr.simulate_candidate_actions(scene, [_two_step_candidate()], steps=2)
        self.assertEqual(len(batch.records), 2)
        self.assertTrue(batch.wait(timeout=5))
        for rec in batch.records:
            self.assertEqual(rec.status, "complete")
        self.assertEqual(len(wm.requests), 2)
        self.assertEqual(wm.requests[0].history, [])
        self.assertEqual(len(wm.requests[1].history), 1)
        self.assertIs(wm.requests[1].history[0], wm.results[0])
        mgr.shutdown(wait=True)

    def test_no_continuation_backend_gets_no_history(self):
        scene = _scene_with_out("shoe")
        wm = FakeWorldModel(delay=0.01, supports_continuation=False)
        mgr = RolloutManager(wm, observation_fn=fake_observation_fn, describe_fn=fake_describe_fn)
        batch = mgr.simulate_candidate_actions(scene, [_two_step_candidate()], steps=2)
        self.assertTrue(batch.wait(timeout=5))
        self.assertEqual(wm.requests[0].history, [])
        self.assertEqual(wm.requests[1].history, [])
        mgr.shutdown(wait=True)

    def test_request_id_deterministic_across_runs(self):
        scene = _scene_with_out("shoe")
        ids = []
        for _ in range(2):
            wm = FakeWorldModel(delay=0.01)
            mgr = RolloutManager(wm, observation_fn=fake_observation_fn, describe_fn=fake_describe_fn)
            batch = mgr.simulate_candidate_actions(scene, [_valid_candidate()], steps=1)
            self.assertTrue(batch.wait(timeout=5))
            ids.append(wm.requests[0].request_id)
            mgr.shutdown(wait=True)
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(len(ids[0]), 12)

    def test_reports_pending_then_complete_with_assets(self):
        scene = _scene_with_out("shoe")
        wm = FakeWorldModel(delay=0.2)
        mgr = RolloutManager(
            wm, observation_fn=fake_observation_fn, describe_fn=fake_describe_fn, evaluate_fn=fake_evaluate_fn
        )
        batch = mgr.simulate_candidate_actions(scene, [_valid_candidate()], steps=1)
        pending_report = batch.reports()[0]
        self.assertEqual(pending_report.simulation_status, "pending")
        self.assertIsNone(pending_report.pan_final_frame)
        self.assertIsNone(pending_report.risk_metadata)

        self.assertTrue(batch.wait(timeout=5))
        done_report = batch.reports()[0]
        self.assertEqual(done_report.simulation_status, "complete")
        self.assertIsNotNone(done_report.pan_final_frame)
        self.assertIsNotNone(done_report.pan_preview_video)
        self.assertIsNotNone(done_report.risk_metadata)
        self.assertEqual(done_report.execution_risk, "low")
        mgr.shutdown(wait=True)

    def test_score_components_present_and_pan_risk_none_when_unavailable(self):
        scene = _scene_with_out("shoe")
        wm = FakeWorldModel(available=False)
        mgr = RolloutManager(wm, observation_fn=fake_observation_fn, describe_fn=fake_describe_fn)
        batch = mgr.simulate_candidate_actions(scene, [_valid_candidate()], steps=1)
        sc = batch.reports()[0].score_components
        for key in ("geometry_score", "stability_score", "pan_risk", "execution_risk_weight", "total"):
            self.assertIn(key, sc)
        self.assertIsNone(sc["pan_risk"])
        mgr.shutdown(wait=True)

    def test_rank_candidates_puts_physics_invalid_last(self):
        scene = _scene_with_out("shoe")
        wm = FakeWorldModel(delay=0.01)
        mgr = RolloutManager(wm, observation_fn=fake_observation_fn, describe_fn=fake_describe_fn)
        batch = mgr.simulate_candidate_actions(scene, [_collision_candidate(), _valid_candidate()], steps=1)
        self.assertTrue(batch.wait(timeout=5))
        ranked = rank_candidates(batch)
        self.assertEqual(ranked[-1].candidate_id, "collision")
        self.assertEqual(ranked[-1].physics_status, "invalid")
        self.assertEqual(ranked[0].candidate_id, "shoe_first")
        mgr.shutdown(wait=True)

    def test_statuses_keys(self):
        scene = _scene_with_out("shoe")
        wm = FakeWorldModel(delay=0.01)
        mgr = RolloutManager(wm, observation_fn=fake_observation_fn, describe_fn=fake_describe_fn)
        batch = mgr.simulate_candidate_actions(scene, [_two_step_candidate()], steps=2)
        statuses = batch.statuses()
        self.assertEqual(set(statuses), {"two_step/0", "two_step/1"})
        self.assertTrue(batch.wait(timeout=5))
        mgr.shutdown(wait=True)

    def test_shutdown_does_not_hang(self):
        scene = _scene_with_out("shoe")
        wm = FakeWorldModel(delay=0.01)
        mgr = RolloutManager(wm, observation_fn=fake_observation_fn, describe_fn=fake_describe_fn)
        batch = mgr.simulate_candidate_actions(scene, [_valid_candidate()], steps=1)
        batch.wait(timeout=5)
        t0 = time.monotonic()
        mgr.shutdown(wait=True)
        self.assertLess(time.monotonic() - t0, 2.0)


if __name__ == "__main__":
    unittest.main()
