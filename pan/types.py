"""Provider-neutral contract for the PAN world-model layer.

Every module under `pan/` speaks these types; nothing outside `pan/world_model.py`
may import a PAN SDK / HTTP detail. Scene geometry, IDs, units and axes are the
physics layer's (`physics.schema`): meters, X=right / Y=up / Z=forward,
quaternion (x, y, z, w), stable string object ids shared across scan → solver →
physics → PAN → renderer.

Scientific boundary (keep it visible in every output): physics results are hard
constraints; everything PAN-derived (`SimulationResult`, `RiskSignals`) is a
learned, probabilistic prediction and must never override a physics violation.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Optional, Protocol, runtime_checkable

import numpy as np

from physics.schema import Object, Scene

SimulationStatus = Literal["pending", "complete", "failed", "unavailable"]
ImageSource = Literal["ios_rgb", "rendered"]


# ---------------------------------------------------------------- observation
@dataclass
class Viewpoint:
    """Deterministic pinhole camera for rendered observations (world frame, meters)."""

    name: str
    camera_position: tuple[float, float, float]
    look_at: tuple[float, float, float]
    up: tuple[float, float, float] = (0.0, 1.0, 0.0)
    fov_deg: float = 60.0
    width: int = 512
    height: int = 512


@dataclass
class Observation:
    """One RGB view of the current world state, plus the mapping back to the scene."""

    image: np.ndarray  # (H, W, 3) uint8 RGB
    scene_id: str
    source: ImageSource
    viewpoint: Optional[Viewpoint] = None  # None for real iPhone frames
    timestamp: float = field(default_factory=time.time)
    image_path: Optional[str] = None  # set when persisted to disk
    object_ids: list[str] = field(default_factory=list)  # objects present in the scene state
    # Free-form but conventional keys: "object_colors": {id: (r,g,b)} for rendered
    # frames (lets the evaluator segment by color), "crop": [x0,y0,x1,y1],
    # "original_size": [w,h], "frame_index" for iOS captures.
    metadata: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------- action
@dataclass
class PackingAction:
    """One grounded physical step: move `object_id` to a target pose.

    `text` is the natural-language action sent to PAN (filled by `pan.actions`);
    keep it derived from the fields below, never hand-typed.
    """

    object_id: str
    target_position: tuple[float, float, float]
    target_rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    order_index: int = 0  # 0-based step within its candidate sequence
    label: Optional[str] = None  # semantic label if known ("black running shoe"); else derived from id
    text: str = ""
    candidate_id: Optional[str] = None


@dataclass
class CandidateSequence:
    """An ordered plan of next actions proposed by the solver (top-K candidates)."""

    candidate_id: str
    actions: list[PackingAction]
    label: str = ""  # human summary, e.g. "shoe first"


def apply_action(scene: Scene, action: PackingAction) -> Scene:
    """Return a NEW scene with `action.object_id` moved to its target pose.
    Raises KeyError if the id is not in the scene. Never mutates `scene`."""
    ids = [o.id for o in scene.objects]
    if action.object_id not in ids:
        raise KeyError(action.object_id)
    objects = [
        replace(o, position=tuple(action.target_position), rotation=tuple(action.target_rotation))
        if o.id == action.object_id
        else o
        for o in scene.objects
    ]
    return Scene(container=scene.container, objects=objects)


def apply_sequence(scene: Scene, actions: list[PackingAction], upto: Optional[int] = None) -> Scene:
    """Apply `actions[:upto]` in order (all of them when `upto` is None)."""
    for a in actions[: len(actions) if upto is None else upto]:
        scene = apply_action(scene, a)
    return scene


# ----------------------------------------------------------------- simulation
@dataclass
class SimulationRequest:
    observation: Observation
    action: PackingAction
    history: list["SimulationResult"] = field(default_factory=list)  # prior steps, for continuation
    options: dict[str, Any] = field(default_factory=dict)  # e.g. {"num_frames": 8, "seed": 0}
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


@dataclass
class SimulationResult:
    """Normalized output of any backend. `frames` may be empty on failure."""

    request_id: str
    status: SimulationStatus
    backend: str  # "mock" | "pan"
    frames: list[np.ndarray] = field(default_factory=list)  # ordered (H, W, 3) uint8 RGB
    video_path: Optional[str] = None  # GIF/MP4 when persisted
    final_frame_path: Optional[str] = None
    latency_ms: float = 0.0
    error: Optional[str] = None  # human-readable, MUST NOT contain secrets/headers
    cache_hit: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def final_frame(self) -> Optional[np.ndarray]:
        return self.frames[-1] if self.frames else None


@runtime_checkable
class WorldModel(Protocol):
    """The only interface the rest of the app depends on."""

    name: str
    supports_continuation: bool  # can `history` seed the next rollout?

    def available(self) -> bool: ...

    def simulate(self, request: SimulationRequest) -> SimulationResult: ...


# ----------------------------------------------------------------- evaluation
@dataclass
class RiskSignals:
    """Learned/uncertain signals extracted from a rollout. Never hard constraints."""

    accessibility_risk: float  # 0..1, higher = later placements look harder to reach
    visible_shift: bool  # a non-acted object appears to move
    possible_topple: bool
    occlusion_risk: float  # 0..1, target region covered by other objects at the end
    confidence: float  # 0..1 in these signals themselves (low for unsegmented real frames)
    evidence: dict[str, Any] = field(default_factory=dict)  # per-object px displacement, areas, ...
    notes: list[str] = field(default_factory=list)

    def level(self) -> str:
        """Coarse label for the UI: low / medium / high."""
        worst = max(self.accessibility_risk, self.occlusion_risk, 1.0 if self.possible_topple else 0.0)
        if worst >= 0.6:
            return "high"
        if worst >= 0.3 or self.visible_shift:
            return "medium"
        return "low"


# ------------------------------------------------------------------- records
@dataclass
class RolloutRecord:
    """One candidate action's full story: physics gate → PAN rollout → risk."""

    candidate_id: str
    action: PackingAction
    physics: dict  # `validate_layout` result for the state AFTER the action (authoritative)
    physics_valid: bool
    status: SimulationStatus
    result: Optional[SimulationResult] = None
    risk: Optional[RiskSignals] = None
    error: Optional[str] = None


@dataclass
class CandidateReport:
    """Renderer/UI contract (PAN.md §20). Plain, JSON-friendly fields only."""

    candidate_id: str
    label: str
    physics_status: Literal["valid", "invalid", "malformed"]
    simulation_status: SimulationStatus
    execution_risk: Optional[str]  # "low" | "medium" | "high" | None
    action_text: str
    pan_preview_video: Optional[str] = None
    pan_final_frame: Optional[str] = None
    risk_metadata: Optional[dict] = None
    # Every component logged separately (PAN.md §19) -- never one opaque score.
    score_components: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "label": self.label,
            "physics_status": self.physics_status,
            "simulation_status": self.simulation_status,
            "execution_risk": self.execution_risk,
            "action_text": self.action_text,
            "pan_preview_video": self.pan_preview_video,
            "pan_final_frame": self.pan_final_frame,
            "risk_metadata": self.risk_metadata,
            "score_components": dict(self.score_components),
        }


def object_by_id(scene: Scene, object_id: str) -> Object:
    for o in scene.objects:
        if o.id == object_id:
            return o
    raise KeyError(object_id)
