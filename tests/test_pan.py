import json
import os
import urllib.error
from unittest.mock import patch

from physics.pan import (
    MockPanBackend,
    PanAction,
    RealPanBackend,
    _load_ifm_api_key,
    build_observation,
    describe_action,
    result_note,
    simulate_candidate_actions,
)
from physics.schema import Container, Object, Scene


def _scene():
    container = Container(id="carry_on", dimensions=(0.56, 0.23, 0.36), position=(0.0, 0.115, 0.0))
    shoe = Object(id="shoe", dimensions=(0.29, 0.11, 0.12), position=(-0.13, 0.055, 0.1))
    other = Object(id="camera", dimensions=(0.13, 0.09, 0.1), position=(0.125, 0.045, 0.1075))
    return Scene(container=container, objects=[shoe, other])


def test_describe_action_is_deterministic_and_grounded():
    scene = _scene()
    action = PanAction(object_id="shoe", target_position=(0.13, 0.055, -0.1), target_rotation=(0.0, 0.7071, 0.0, 0.7071))
    text_a = describe_action(scene, action)
    text_b = describe_action(scene, action)
    assert text_a == text_b
    assert "shoe" in text_a
    assert "front-right" in text_a  # target_position is +x, -z -> rel_x>0 "right", rel_z<0 "front"
    assert "rotated approximately 90 degrees counterclockwise" in text_a


def test_describe_action_unknown_object_raises():
    scene = _scene()
    action = PanAction(object_id="missing", target_position=(0, 0, 0))
    try:
        describe_action(scene, action)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_mock_backend_deterministic():
    backend = MockPanBackend()
    obs = build_observation("scene_1").as_dict()
    r1 = backend.simulate(obs, "place the shoe")
    r2 = backend.simulate(obs, "place the shoe")
    assert r1.status == "complete"
    assert r1.video_path == r2.video_path
    assert r1.backend == "mock"


def test_build_observation_marks_unavailable_without_image():
    obs = build_observation("scene_1")
    assert obs.image_source == "unavailable"
    assert obs.image_path is None


def test_simulate_candidate_actions_gates_on_physics():
    scene = _scene()
    valid_action = PanAction(object_id="shoe", target_position=(0.13, 0.055, -0.1))
    colliding_action = PanAction(object_id="shoe", target_position=(0.125, 0.045, 0.1075))  # camera's spot

    results = simulate_candidate_actions(scene, "scene_1", [valid_action, colliding_action], MockPanBackend())

    assert results[0]["physics_valid"] is True
    assert results[0]["pan"].status == "complete"

    assert results[1]["physics_valid"] is False
    assert results[1]["pan"].status == "unavailable"
    assert results[1]["pan"].error == "physics_invalid"


def test_real_pan_backend_unavailable_without_key():
    with patch("physics.pan._load_ifm_api_key", return_value=None):
        backend = RealPanBackend()
    result = backend.simulate({"scene_id": "s"}, "place the shoe")
    assert result.status == "unavailable"
    assert result.error == "no IFM_API_KEY configured"


def test_real_pan_backend_parses_json_schema_response():
    fake_response = json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "accessibility_risk": 0.2,
                                "visible_shift": False,
                                "possible_topple": False,
                                "occlusion_risk": 0.1,
                                "confidence": 0.9,
                                "rationale": "low risk",
                            }
                        )
                    }
                }
            ]
        }
    ).encode("utf-8")

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return fake_response

    backend = RealPanBackend(api_key="fake-key")
    with patch("physics.pan.urllib.request.urlopen", return_value=_FakeResp()):
        result = backend.simulate({"scene_id": "s"}, "place the shoe")

    assert result.status == "complete"
    assert result.backend == "ifm-k2-horizon"
    assert result.metadata["risk"]["accessibility_risk"] == 0.2
    # The display path (examples/pan_demo.py) prints this verbatim: a K2-Horizon
    # answer must never be shown as if it were a visual PAN rollout.
    assert result_note(result) == "textual reasoning, not a visual PAN rollout"


def test_timeout_is_retried_once_then_reported_failed():
    backend = RealPanBackend(api_key="fake-key")
    assert backend.timeout == 20.0  # per call, so the worst case is 40s not one 45s hang
    with patch("physics.pan.urllib.request.urlopen", side_effect=TimeoutError("timed out")) as urlopen, \
            patch("physics.pan._LOG"):  # the retry warning is expected here, not a live call
        result = backend.simulate({"scene_id": "s"}, "place the shoe")
    assert urlopen.call_count == 2  # one retry, not an infinite loop
    assert result.status == "failed"


def test_http_401_is_not_retried():
    err = urllib.error.HTTPError("https://api.ifm.ai/v1/chat/completions", 401, "Unauthorized", {}, None)
    backend = RealPanBackend(api_key="fake-key")
    with patch("physics.pan.urllib.request.urlopen", side_effect=err) as urlopen:
        result = backend.simulate({"scene_id": "s"}, "place the shoe")
    assert urlopen.call_count == 1  # a bad key is not fixed by asking again
    assert result.status == "failed"


def test_pan_api_key_is_an_accepted_alias_for_ifm_api_key():
    with patch.dict(os.environ, {"PAN_API_KEY": "aliased-key"}, clear=True):
        assert _load_ifm_api_key() == "aliased-key"


def test_result_note_labels_every_displayable_result():
    assert result_note(MockPanBackend().simulate(build_observation("s").as_dict(), "place the shoe")) == (
        "deterministic stand-in, not a world-model prediction"
    )
    # nothing to mislabel when no rollout ran
    gated = simulate_candidate_actions(
        _scene(), "s", [PanAction(object_id="shoe", target_position=(0.125, 0.045, 0.1075))], MockPanBackend()
    )[0]["pan"]
    assert gated.status == "unavailable"
    assert result_note(gated) is None
