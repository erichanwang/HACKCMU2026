"""Provider-neutral world-model layer between the physics validator and IFM PAN.

PAN.md is the design doc this implements. Section 10 of that doc requires
confirming real PAN access before writing a client. Verified 2026-09-12,
with a working API key in `.env`: IFM's actual public API
(https://docs.ifm.ai/, https://api.ifm.ai/v1) is an OpenAI-style chat
completions endpoint serving one hosted model, `IFM/K2-Horizon-375B-A23B` --
a text reasoning model. There is no image/video generation endpoint, no
vision input on the hosted model, and no "PAN" model or world-model endpoint
anywhere on that platform. The visual world model described in the PAN paper
(arxiv.org/abs/2511.09057) and ifm.ai/pan/ is not part of this API surface.

So `RealPanBackend` below does NOT produce a visual rollout -- it asks
K2 Horizon to reason in text about the physical consequences of a placement
action and returns structured risk signals (PAN.md section 5, Level 3),
which is the honest ceiling of what "real PAN" can mean with the access
that exists. `MockPanBackend` remains for offline dev/tests. Both implement
`WorldModel`, so callers don't care which is active.

PAN never overrides the deterministic physics layer (physics.validator). It
answers a different question: not "is this layout valid?" (validator.py) but
"what might happen if a traveler carries out this placement action?".
`simulate_candidate_actions` enforces that ordering -- physics-invalid
candidates never reach the world model.
"""
from __future__ import annotations

import hashlib
import json as _json
import math
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol

from physics.io import apply_placements
from physics.schema import Container, Scene
from physics.validator import validate_layout

IFM_BASE_URL = "https://api.ifm.ai/v1"
IFM_MODEL = "IFM/K2-Horizon-375B-A23B"  # only model with hosted API access, per docs.ifm.ai/#/model-catalog
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _load_ifm_api_key() -> Optional[str]:
    """`IFM_API_KEY` env var, else `.env` at the repo root (either
    `IFM_API_KEY=...` or a bare token on its own line)."""
    key = os.environ.get("IFM_API_KEY")
    if key:
        return key.strip()
    if not _ENV_PATH.exists():
        return None
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            name, _, value = line.partition("=")
            if name.strip() == "IFM_API_KEY":
                return value.strip().strip('"')
        else:
            return line
    return None


@dataclass
class PanAction:
    """A single proposed next placement, in the vocabulary the solver already
    uses (physics.io.apply_placements' placement dict)."""

    object_id: str
    target_position: tuple[float, float, float]
    target_rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    order: Optional[int] = None


@dataclass
class SimulationResult:
    status: str  # "pending" | "complete" | "failed" | "unavailable"
    backend: str
    video_path: Optional[str] = None
    final_frame_path: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    latency_ms: float = 0.0
    error: Optional[str] = None


class WorldModel(Protocol):
    name: str

    def simulate(self, observation: dict, action_text: str) -> SimulationResult: ...


class MockPanBackend:
    """Deterministic stand-in for IFM PAN. No network call, no learned
    prediction -- same (scene, action) always returns the same result so the
    rest of the pipeline (caching, UI, tests) can be built and demoed before
    real PAN access is confirmed."""

    name = "mock"

    def simulate(self, observation: dict, action_text: str) -> SimulationResult:
        start = time.monotonic()
        digest = hashlib.sha1(f"{observation.get('scene_id')}::{action_text}".encode()).hexdigest()[:12]
        return SimulationResult(
            status="complete",
            backend=self.name,
            video_path=f"mock://pan/{digest}.mp4",
            final_frame_path=f"mock://pan/{digest}.png",
            metadata={"mock": True, "action_text": action_text, "observation": observation},
            latency_ms=(time.monotonic() - start) * 1000.0,
        )


_RISK_SCHEMA = {
    "name": "execution_risk",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "accessibility_risk": {"type": "number"},
            "visible_shift": {"type": "boolean"},
            "possible_topple": {"type": "boolean"},
            "occlusion_risk": {"type": "number"},
            "confidence": {"type": "number"},
            "rationale": {"type": "string"},
        },
        "required": [
            "accessibility_risk",
            "visible_shift",
            "possible_topple",
            "occlusion_risk",
            "confidence",
            "rationale",
        ],
    },
}


class RealPanBackend:
    """Textual simulative-reasoning backend using IFM's hosted K2 Horizon
    model. See the module docstring for why this isn't a visual rollout.

    Never raises (PAN.md section 12: sponsor APIs fail, the pipeline must
    not depend on it) -- a missing key, network error, or malformed
    response all come back as a `SimulationResult` with `status` set
    instead of an exception.
    """

    name = "ifm-k2-horizon"

    def __init__(self, api_key: Optional[str] = None, *, model: str = IFM_MODEL, timeout: float = 45.0):
        self.api_key = api_key or _load_ifm_api_key()
        self.model = model
        self.timeout = timeout

    def simulate(self, observation: dict, action_text: str) -> SimulationResult:
        if not self.api_key:
            return SimulationResult(status="unavailable", backend=self.name, error="no IFM_API_KEY configured")

        prompt = (
            "You are a physical-world reasoning assistant. A traveler is about to perform this packing "
            "action inside a suitcase:\n\n"
            f"{action_text}\n\n"
            "Estimate the real-world execution risk of this action: could the traveler struggle to reach "
            "or see the target spot, could nearby items shift or topple, could this block access to items "
            "placed later? Respond with the requested JSON only."
        )
        body = _json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_schema", "json_schema": _RISK_SCHEMA},
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{IFM_BASE_URL}/chat/completions",
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )

        start = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = _json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError) as e:
            return SimulationResult(
                status="failed", backend=self.name, error=str(e), latency_ms=(time.monotonic() - start) * 1000.0
            )

        latency_ms = (time.monotonic() - start) * 1000.0
        try:
            risk = _json.loads(payload["choices"][0]["message"]["content"])
        except (KeyError, IndexError, ValueError) as e:
            return SimulationResult(
                status="failed", backend=self.name, error=f"unexpected response shape: {e}", latency_ms=latency_ms
            )

        return SimulationResult(
            status="complete",
            backend=self.name,
            video_path=None,
            final_frame_path=None,
            metadata={"risk": risk, "action_text": action_text, "note": "textual reasoning, not a visual PAN rollout"},
            latency_ms=latency_ms,
        )


# --- Action language generation ---------------------------------------------


def _yaw_deg(rotation: tuple[float, float, float, float]) -> float:
    """Degrees of rotation about world Y, assuming a yaw-only quaternion (the
    convention physics.io.object_from_box_fit produces and validate_layout
    consumes elsewhere in this repo)."""
    _, y, _, w = rotation
    return math.degrees(2.0 * math.atan2(y, w))


def _region_phrase(position: tuple[float, float, float], container: Container) -> str:
    rel_x = position[0] - container.position[0]
    rel_z = position[2] - container.position[2]
    lr = "left" if rel_x < 0 else "right"
    fb = "front" if rel_z < 0 else "rear"
    return f"{fb}-{lr}"


def describe_action(scene: Scene, action: PanAction) -> str:
    """Grounded natural-language action text for PAN, built deterministically
    from solver output (PAN.md section 8) -- no invented object attributes."""
    obj = next((o for o in scene.objects if o.id == action.object_id), None)
    if obj is None:
        raise ValueError(f"unknown object id: {action.object_id}")

    label = obj.id.replace("_", " ")
    from_region = _region_phrase(obj.position, scene.container)
    to_region = _region_phrase(action.target_position, scene.container)
    text = (
        f"A traveler reaches for the {label} positioned in the {from_region} of the "
        f"{scene.container.id} and places it into the {to_region} corner."
    )

    rotation_delta = _yaw_deg(action.target_rotation) - _yaw_deg(obj.rotation)
    rotation_delta = ((rotation_delta + 180.0) % 360.0) - 180.0  # wrap to [-180, 180)
    if abs(rotation_delta) > 1.0:
        direction = "clockwise" if rotation_delta < 0 else "counterclockwise"
        text += f" It is rotated approximately {abs(round(rotation_delta))} degrees {direction}."

    text += " The other objects remain in their current positions."
    return text


# --- Scene observation adapter (PAN.md section 7) ----------------------------


@dataclass
class PanObservation:
    scene_id: str
    viewpoint: str
    image_source: str  # "live_camera" | "rendered_digital_twin" | "unavailable"
    image_path: Optional[str]
    timestamp: float

    def as_dict(self) -> dict:
        return {
            "scene_id": self.scene_id,
            "viewpoint": self.viewpoint,
            "image_source": self.image_source,
            "image_path": self.image_path,
            "timestamp": self.timestamp,
        }


def build_observation(
    scene_id: str,
    *,
    viewpoint: str = "front_three_quarter",
    image_path: Optional[str] = None,
    image_source: Optional[str] = None,
) -> PanObservation:
    """Mode A (real iPhone RGB frame) or Mode B (rendered digital twin) --
    caller passes whichever `image_path` it has. No renderer exists in this
    repo yet, so with no `image_path` the observation is honestly marked
    `"unavailable"` rather than faked."""
    if image_source is None:
        image_source = "unavailable" if image_path is None else "rendered_digital_twin"
    return PanObservation(
        scene_id=scene_id,
        viewpoint=viewpoint,
        image_source=image_source,
        image_path=image_path,
        timestamp=time.time(),
    )


# --- Counterfactual rollout manager (PAN.md sections 5, 15/Subagent 5, 17) ---


def simulate_candidate_actions(
    scene: Scene,
    scene_id: str,
    candidates: list[PanAction],
    backend: WorldModel,
    *,
    require_physics_valid: bool = True,
) -> list[dict]:
    """For each candidate next action: validate physics first (cheap,
    deterministic), and only spend a world-model call on candidates that
    pass. Never lets PAN latency/unavailability block the physics-valid
    result -- a failed/unavailable simulate() just leaves `pan` as that
    status, the candidate list is still returned.
    """
    results = []
    for action in candidates:
        text = describe_action(scene, action)
        placed = apply_placements(
            scene, [{"id": action.object_id, "position": action.target_position, "rotation": action.target_rotation}]
        )
        physics = validate_layout(placed)
        entry = {
            "object_id": action.object_id,
            "order": action.order,
            "action_text": text,
            "physics_valid": physics["valid"],
            "physics_score": physics["score"],
        }
        if require_physics_valid and not physics["valid"]:
            entry["pan"] = SimulationResult(status="unavailable", backend=backend.name, error="physics_invalid")
        else:
            observation = build_observation(scene_id)
            try:
                entry["pan"] = backend.simulate(observation.as_dict(), text)
            except Exception as e:  # PAN.md section 12: sponsor APIs fail, never block the demo
                entry["pan"] = SimulationResult(status="failed", backend=backend.name, error=str(e))
        results.append(entry)
    return results
