"""Offline unittest coverage for pan.world_model (Mock, Real, Caching, factory)."""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import unittest
from dataclasses import replace
from functools import lru_cache
from unittest import mock

import numpy as np
from PIL import Image

from pan.demo import persist_result
from pan.evaluate import DEFAULT_THRESHOLDS, evaluate_rollout, segment_by_color
from pan.observation import observation_from_scene, project_points, render_scene
from pan.types import Observation, PackingAction, SimulationRequest, SimulationResult, honesty_note
from pan.world_model import (
    CachingWorldModel,
    MockPanBackend,
    RealPanBackend,
    get_world_model,
)
from physics.schema import Scene
from tests.fixtures import scene_with_precarious_balance, valid_packed_scene

# The IFM key the one real backend reads, plus the alias Eric's .env uses.
_PAN_ENV_KEYS = ["IFM_API_KEY", "PAN_API_KEY"]


@contextlib.contextmanager
def _clean_pan_env():
    """No key from the real environment (nor from a repo-root `.env`) leaks in."""
    saved = {k: os.environ.pop(k, None) for k in _PAN_ENV_KEYS}
    try:
        with mock.patch("physics.pan._load_ifm_api_key", return_value=None):
            yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def _make_image(size: int = 24) -> np.ndarray:
    img = np.full((size, size, 3), 100, dtype=np.uint8)  # gray background
    # 6x6 blocks: comfortably over pan.evaluate's min_area, so both count as
    # segmented objects rather than noise.
    img[2:8, 2:8] = (255, 0, 0)  # "shoe" block, top-left
    img[size - 8:size - 2, size - 8:size - 2] = (0, 255, 0)  # "camera" block, bottom-right
    return img


def _make_observation(with_colors: bool = True, size: int = 24) -> Observation:
    image = _make_image(size)
    metadata = {"object_colors": {"shoe": (255, 0, 0), "camera": (0, 255, 0)}} if with_colors else {}
    return Observation(image=image, scene_id="scene-1", source="rendered", object_ids=["shoe", "camera"], metadata=metadata)


def _make_action(target=(0.5, 0.0, 0.0)) -> PackingAction:
    return PackingAction(
        object_id="shoe",
        target_position=target,
        text="place the shoe heel-first into the rear-right corner",
    )


# --------------------------------------------------------------- rendered fixtures
_RENDER_PX = 384  # same size pan/render_fixtures.py writes; keeps the suite quick


def _table_scene(x: float = -0.7) -> Scene:
    """valid_packed_scene with the shoe lying on the table beside the suitcase."""
    scene = valid_packed_scene()
    return Scene(
        container=scene.container,
        objects=[replace(o, position=(x, 0.055, 0.10)) if o.id == "shoe" else o for o in scene.objects],
    )


def _shoe_home() -> tuple[float, float, float]:
    return next(o for o in valid_packed_scene().objects if o.id == "shoe").position


@lru_cache(maxsize=8)
def _rendered(x: float = -0.7, frame: str = "scene") -> Observation:
    return observation_from_scene(_table_scene(x), "overhead_45", width=_RENDER_PX, height=_RENDER_PX, frame=frame)


def _put_the_shoe_back() -> PackingAction:
    return PackingAction(object_id="shoe", target_position=_shoe_home(), text="put the shoe back into the case")


def _blob(frame: np.ndarray, colors: dict, object_id: str):
    """Segment one object exactly the way pan.evaluate does. None if absent."""
    mask = segment_by_color(frame, colors)[object_id]
    if not mask.any():
        return None
    ys, xs = np.nonzero(mask)
    return {
        "area": int(mask.sum()),
        "centroid": (float(xs.mean()), float(ys.mean())),
        "width": int(xs.max() - xs.min()) + 1,
        "height": int(ys.max() - ys.min()) + 1,
    }


class MockDeterminismTests(unittest.TestCase):
    def test_same_inputs_give_byte_identical_frames(self):
        backend = MockPanBackend()
        obs = _make_observation()
        action = _make_action()
        r1 = backend.simulate(SimulationRequest(observation=obs, action=action))
        r2 = backend.simulate(SimulationRequest(observation=obs, action=action))
        self.assertNotEqual(r1.request_id, r2.request_id)  # sanity: not literally the same request
        self.assertEqual(len(r1.frames), len(r2.frames))
        for f1, f2 in zip(r1.frames, r2.frames):
            self.assertTrue(np.array_equal(f1, f2))

    def test_num_frames_respected(self):
        backend = MockPanBackend()
        req = SimulationRequest(observation=_make_observation(), action=_make_action(), options={"num_frames": 5})
        result = backend.simulate(req)
        self.assertEqual(len(result.frames), 5)
        self.assertEqual(result.metadata["num_frames"], 5)

    def test_frame_shapes_match_observation(self):
        backend = MockPanBackend()
        obs = _make_observation(size=32)
        result = backend.simulate(SimulationRequest(observation=obs, action=_make_action()))
        for frame in result.frames:
            self.assertEqual(frame.shape, obs.image.shape)
            self.assertEqual(frame.dtype, np.uint8)

    def test_placement_moves_acted_object_and_leaves_other_exactly_fixed(self):
        backend = MockPanBackend()
        obs = _make_observation(size=32)
        action = _make_action(target=(0.5, 0.0, 0.0))
        result = backend.simulate(SimulationRequest(observation=obs, action=action))
        self.assertEqual(result.metadata["mode"], "placement")
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.backend, "mock")
        self.assertEqual(result.metadata["hints_applied"], [])

        colors = obs.metadata["object_colors"]
        first_shoe = _blob(result.frames[0], colors, "shoe")
        last_shoe = _blob(result.frames[-1], colors, "shoe")
        self.assertNotEqual(first_shoe["centroid"], last_shoe["centroid"])  # it moved
        self.assertEqual(first_shoe["area"], last_shoe["area"])  # ...whole, not shredded

        # non-acted object: byte-exact, so its centroid cannot have drifted at all
        first_camera = _blob(result.frames[0], colors, "camera")
        last_camera = _blob(result.frames[-1], colors, "camera")
        self.assertEqual(first_camera, last_camera)

    def test_static_mode_without_color_map(self):
        backend = MockPanBackend()
        obs = _make_observation(with_colors=False)
        result = backend.simulate(SimulationRequest(observation=obs, action=_make_action()))
        self.assertEqual(result.metadata["mode"], "static")
        self.assertIn("note", result.metadata)
        self.assertEqual(len(result.frames), 8)
        self.assertTrue(np.array_equal(result.frames[0], obs.image))
        # nothing translates: every later frame is frame 0 +/- the tiny noise only
        for frame in result.frames[1:]:
            self.assertEqual(frame.shape, obs.image.shape)
            self.assertLessEqual(int(np.abs(frame.astype(int) - obs.image.astype(int)).max()), 2)
        self.assertFalse(all(np.array_equal(result.frames[0], f) for f in result.frames[1:]))

    def test_static_mode_when_the_acted_object_has_no_color(self):
        backend = MockPanBackend()
        obs = _make_observation()
        action = PackingAction(object_id="umbrella", target_position=(0.0, 0.0, 0.0), text="pack the umbrella")
        result = backend.simulate(SimulationRequest(observation=obs, action=action))
        self.assertEqual(result.metadata["mode"], "static")

    def test_continuation_uses_history_last_frame(self):
        backend = MockPanBackend()
        obs = _make_observation()
        distinct_final = np.full_like(obs.image, 7)  # obviously not obs.image
        prior = SimulationResult(request_id="prev", status="complete", backend="mock", frames=[obs.image, distinct_final])
        result = backend.simulate(SimulationRequest(observation=obs, action=_make_action(), history=[prior]))
        self.assertTrue(np.array_equal(result.frames[0], distinct_final))
        self.assertFalse(np.array_equal(result.frames[0], obs.image))

    def test_persist_writes_pngs_and_reloadable_gif(self):
        backend = MockPanBackend()
        req = SimulationRequest(observation=_make_observation(), action=_make_action(), options={"num_frames": 6})
        result = backend.simulate(req)
        with tempfile.TemporaryDirectory() as tmp:
            persisted = persist_result(result, tmp)  # pan.demo is the one persister
            png_files = sorted(p for p in os.listdir(tmp) if p.endswith(".png"))
            self.assertEqual(len(png_files), 6)
            self.assertTrue(os.path.exists(persisted.video_path))
            self.assertEqual(persisted.final_frame_path, os.path.join(tmp, "frame_05.png"))
            with Image.open(persisted.video_path) as gif:
                self.assertEqual(gif.n_frames, 6)


class MockRenderedRolloutTests(unittest.TestCase):
    """The mock against real rendered observations -- the regime the demo runs
    in, and the one the evaluator reads."""

    def setUp(self):
        self.backend = MockPanBackend()

    def test_rendered_rollout_is_deterministic(self):
        obs, action = _rendered(), _put_the_shoe_back()
        a = self.backend.simulate(SimulationRequest(observation=obs, action=action))
        b = self.backend.simulate(SimulationRequest(observation=obs, action=action))
        self.assertNotEqual(a.request_id, b.request_id)
        for f1, f2 in zip(a.frames, b.frames):
            self.assertTrue(np.array_equal(f1, f2))
        self.assertEqual(a.metadata, b.metadata)

    def test_whole_object_lands_on_the_projected_target(self):
        obs, action = _rendered(), _put_the_shoe_back()
        result = self.backend.simulate(SimulationRequest(observation=obs, action=action))
        self.assertEqual(result.metadata["mode"], "placement")

        colors = obs.metadata["object_colors"]
        first = _blob(result.frames[0], colors, "shoe")
        last = _blob(result.frames[-1], colors, "shoe")
        target = project_points([action.target_position], obs.viewpoint)[0]

        self.assertLess(
            float(np.hypot(last["centroid"][0] - target[0], last["centroid"][1] - target[1])),
            5.0,
            "the blob should end up on the pixel the renderer would draw the target pose at",
        )
        # the WHOLE object travels -- shaded side faces included, no split blob
        self.assertGreater(last["area"], first["area"] * 0.75)
        self.assertLess(last["area"], first["area"] * 1.25)

    def test_non_acted_objects_do_not_move(self):
        """Pixel-exact for every object the placed one does not cover; for one it
        does cover, no worse than the ground-truth render of the final pose."""
        obs, action = _rendered(), _put_the_shoe_back()
        result = self.backend.simulate(SimulationRequest(observation=obs, action=action))
        colors = obs.metadata["object_colors"]
        truth_image, _ = render_scene(valid_packed_scene(), obs.viewpoint)  # same camera, shoe home

        shoe_last = segment_by_color(result.frames[-1], colors)["shoe"]
        for oid in colors:
            if oid == "shoe":
                continue
            first = _blob(result.frames[0], colors, oid)
            last = _blob(result.frames[-1], colors, oid)
            covered = bool((segment_by_color(result.frames[0], colors)[oid] & shoe_last).any())
            if not covered:
                self.assertEqual(first, last, f"{oid} must be untouched, byte for byte")
                continue
            truth = _blob(truth_image, colors, oid)
            moved = np.hypot(last["centroid"][0] - first["centroid"][0], last["centroid"][1] - first["centroid"][1])
            truth_moved = np.hypot(
                truth["centroid"][0] - first["centroid"][0], truth["centroid"][1] - first["centroid"][1]
            )
            self.assertLessEqual(
                moved, truth_moved + 2.0,
                f"{oid} is covered by the placed shoe; the mock must not exaggerate that",
            )

    def test_arrival_when_the_acted_object_is_off_screen(self):
        obs = _rendered(x=-0.95, frame="container")  # container framing crops the table item out
        action = _put_the_shoe_back()
        self.assertFalse(obs.metadata["object_visible"]["shoe"])

        result = self.backend.simulate(SimulationRequest(observation=obs, action=action))
        self.assertEqual(result.metadata["mode"], "arrival")
        self.assertEqual(result.metadata["arrival_size_source"], "metadata.object_bboxes")

        colors = obs.metadata["object_colors"]
        first = _blob(result.frames[0], colors, "shoe")
        last = _blob(result.frames[-1], colors, "shoe")
        first_area = 0 if first is None else first["area"]
        self.assertLess(first_area, 0.02 * last["area"], "the shoe is not really in frame 0")
        self.assertGreater(last["area"], DEFAULT_THRESHOLDS["min_area"])
        # it arrived AT the target, not somewhere random
        target = project_points([action.target_position], obs.viewpoint)[0]
        self.assertLess(
            float(np.hypot(last["centroid"][0] - target[0], last["centroid"][1] - target[1])), 8.0
        )

    def test_arrival_size_can_come_from_options(self):
        obs = _rendered(x=-0.95, frame="container")
        result = self.backend.simulate(
            SimulationRequest(
                observation=obs,
                action=_put_the_shoe_back(),
                options={"acted_bbox_px": [0, 0, 40, 20]},
            )
        )
        self.assertEqual(result.metadata["arrival_size_source"], "options.acted_bbox_px")
        self.assertEqual(result.metadata["arrival_size_px"], [40, 20])

    def test_physics_hint_makes_the_evaluator_see_a_topple(self):
        scene = scene_with_precarious_balance()
        obs = observation_from_scene(scene, "overhead_45", width=_RENDER_PX, height=_RENDER_PX)
        camera = next(o for o in scene.objects if o.id == "camera")
        action = PackingAction(object_id="camera", target_position=camera.position, text="rest the camera on the bag")

        plain = self.backend.simulate(SimulationRequest(observation=obs, action=action))
        hinted = self.backend.simulate(
            SimulationRequest(observation=obs, action=action, options={"physics_hints": ["UNSTABLE_STACK"]})
        )
        self.assertEqual(plain.metadata["hints_applied"], [])
        self.assertEqual(hinted.metadata["hints_applied"], ["UNSTABLE_STACK"])

        plain_risk = evaluate_rollout(plain, obs, action)
        hinted_risk = evaluate_rollout(hinted, obs, action)
        self.assertEqual(plain_risk.level(), "low")
        self.assertFalse(plain_risk.possible_topple)
        self.assertFalse(plain_risk.visible_shift)
        self.assertTrue(hinted_risk.possible_topple or hinted_risk.visible_shift)
        self.assertNotEqual(hinted_risk.level(), "low")

    def test_unstable_support_chain_hint_is_honoured_too(self):
        obs, action = _rendered(), _put_the_shoe_back()
        result = self.backend.simulate(
            SimulationRequest(
                observation=obs, action=action,
                options={"physics_hints": ["UNSTABLE_SUPPORT_CHAIN", "SOME_OTHER_WARNING"]},
            )
        )
        self.assertEqual(result.metadata["hints_applied"], ["UNSTABLE_SUPPORT_CHAIN"])
        self.assertTrue(evaluate_rollout(result, obs, action).possible_topple)

    def test_soft_compression_hint_squashes_the_blob(self):
        obs, action = _rendered(), _put_the_shoe_back()
        plain = self.backend.simulate(SimulationRequest(observation=obs, action=action))
        soft = self.backend.simulate(
            SimulationRequest(observation=obs, action=action, options={"physics_hints": ["SOFT_COMPRESSION"]})
        )
        self.assertEqual(soft.metadata["hints_applied"], ["SOFT_COMPRESSION"])
        colors = obs.metadata["object_colors"]

        def height(frame):
            # percentile spread, not min/max: nearest-color segmentation picks up
            # a handful of stray pixels elsewhere in the frame that would pin the
            # raw bbox (that is `segment_by_color`'s behaviour, not the mock's).
            ys = np.nonzero(segment_by_color(frame, colors)["shoe"])[0]
            return float(np.percentile(ys, 98) - np.percentile(ys, 2))

        plain_h, soft_h = height(plain.frames[-1]), height(soft.frames[-1])
        self.assertLess(soft_h, plain_h * 0.92)
        self.assertGreater(soft_h, plain_h * 0.78)  # ~15%, not a collapse

    def test_end_to_end_putting_the_shoe_back_reads_as_low_risk(self):
        """The smoke case that motivated all of this: the acted object starts on
        the table (in shot, thanks to scene framing), so the mock places it
        instead of wobbling and the evaluator sees a clean, low-risk execution."""
        scene = _table_scene()
        obs = observation_from_scene(scene, width=_RENDER_PX, height=_RENDER_PX)  # default framing
        action = _put_the_shoe_back()
        result = self.backend.simulate(SimulationRequest(observation=obs, action=action))
        risk = evaluate_rollout(result, obs, action)
        self.assertEqual(result.metadata["mode"], "placement")
        self.assertFalse(risk.visible_shift)
        self.assertFalse(risk.possible_topple)
        self.assertEqual(risk.level(), "low")

    def test_latency_is_reported_and_sane(self):
        obs, action = _rendered(), _put_the_shoe_back()
        result = self.backend.simulate(SimulationRequest(observation=obs, action=action))
        self.assertGreater(result.latency_ms, 0.0)
        self.assertLess(result.latency_ms, 2000.0)


class _FakeIfmResponse:
    """Stands in for the urlopen() context manager physics.pan uses."""

    def __init__(self, risk: dict):
        self._body = json.dumps({"choices": [{"message": {"content": json.dumps(risk)}}]}).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


_FAKE_RISK = {
    "accessibility_risk": 0.2,
    "visible_shift": False,
    "possible_topple": False,
    "occlusion_risk": 0.1,
    "confidence": 0.9,
    "rationale": "low risk",
}


class RealBackendTests(unittest.TestCase):
    """The one real backend: `physics.pan.RealPanBackend` (IFM K2-Horizon,
    text) behind the `pan.types.WorldModel` interface."""

    def test_unavailable_without_a_key(self):
        with _clean_pan_env():
            backend = RealPanBackend()
            self.assertFalse(backend.available())
            result = backend.simulate(SimulationRequest(observation=_make_observation(), action=_make_action()))
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.frames, [])
        self.assertIsNotNone(result.error)

    def test_pan_api_key_is_accepted_as_an_ifm_key_alias(self):
        # clear=True so IFM_API_KEY cannot be the thing that made it available
        with mock.patch.dict(os.environ, {"PAN_API_KEY": "fake-key"}, clear=True):
            self.assertTrue(RealPanBackend().available())
            self.assertEqual(get_world_model(prefer="auto").name, "ifm-k2-horizon")

    def test_complete_result_carries_risk_and_no_frames(self):
        backend = RealPanBackend()
        backend.client.api_key = "fake-key"
        with mock.patch("physics.pan.urllib.request.urlopen", return_value=_FakeIfmResponse(_FAKE_RISK)):
            result = backend.simulate(SimulationRequest(observation=_make_observation(), action=_make_action()))

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.backend, "ifm-k2-horizon")
        self.assertEqual(result.frames, [])  # text reasoning: there is no visual rollout
        self.assertEqual(result.metadata["risk"]["accessibility_risk"], 0.2)
        # ...and nothing downstream may show it as one
        self.assertIn("not a visual PAN rollout", honesty_note(result.backend, result.metadata))
        self.assertIsNone(evaluate_rollout(result, _make_observation(), _make_action()))

    def test_failure_is_reported_not_raised(self):
        backend = RealPanBackend()
        backend.client.api_key = "fake-key"
        with mock.patch("physics.pan.urllib.request.urlopen", side_effect=TimeoutError("timed out")), \
                mock.patch("physics.pan._LOG"):  # the retry warning is expected here, not a live call
            result = backend.simulate(SimulationRequest(observation=_make_observation(), action=_make_action()))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.frames, [])


class _CountingFakeBackend:
    """Minimal WorldModel stand-in that counts real simulate() calls."""

    name = "fake"
    supports_continuation = False

    def __init__(self):
        self.calls = 0

    def available(self) -> bool:
        return True

    def simulate(self, request: SimulationRequest) -> SimulationResult:
        self.calls += 1
        return SimulationResult(
            request_id=request.request_id,
            status="complete",
            backend="fake",
            frames=[np.zeros((4, 4, 3), dtype=np.uint8)],
        )


class CachingWorldModelTests(unittest.TestCase):
    def test_second_identical_call_is_cache_hit_and_inner_called_once(self):
        inner = _CountingFakeBackend()
        with tempfile.TemporaryDirectory() as tmp:
            cached_model = CachingWorldModel(inner, tmp)
            obs = _make_observation()
            action = _make_action()

            r1 = cached_model.simulate(SimulationRequest(observation=obs, action=action))
            r2 = cached_model.simulate(SimulationRequest(observation=obs, action=action))

        self.assertFalse(r1.cache_hit)
        self.assertTrue(r2.cache_hit)
        self.assertEqual(inner.calls, 1)
        self.assertTrue(np.array_equal(r1.frames[0], r2.frames[0]))

    def test_failures_are_not_cached(self):
        class _FailingBackend(_CountingFakeBackend):
            def simulate(self, request):
                self.calls += 1
                return SimulationResult(request_id=request.request_id, status="failed", backend="fake", error="boom")

        inner = _FailingBackend()
        with tempfile.TemporaryDirectory() as tmp:
            cached_model = CachingWorldModel(inner, tmp)
            obs = _make_observation()
            action = _make_action()
            cached_model.simulate(SimulationRequest(observation=obs, action=action))
            cached_model.simulate(SimulationRequest(observation=obs, action=action))
        self.assertEqual(inner.calls, 2)  # never served from cache


class FactoryTests(unittest.TestCase):
    def test_prefer_mock_returns_mock(self):
        model = get_world_model(prefer="mock")
        self.assertEqual(model.name, "mock")
        self.assertIsInstance(model, MockPanBackend)

    def test_auto_without_env_returns_mock(self):
        with _clean_pan_env():
            model = get_world_model(prefer="auto")
        self.assertEqual(model.name, "mock")

    def test_cache_dir_wraps_in_caching_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = get_world_model(prefer="mock", cache_dir=tmp)
        self.assertIsInstance(model, CachingWorldModel)
        self.assertEqual(model.name, "cache(mock)")


if __name__ == "__main__":
    unittest.main()
