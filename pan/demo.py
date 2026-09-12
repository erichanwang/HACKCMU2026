"""Deterministic end-to-end demo pipeline (PAN.md sec 13/14/15 Subagent 7).

Builds a fixed, partially-packed carry-on scene (shoe + camera pulled out
onto the table beside the suitcase; everything else already packed -- see
`tests.fixtures.valid_packed_scene`), three K=3 candidate next-action
sequences (PAN.md sec 18), and runs them through
`pan.rollouts.RolloutManager`: physics gate -> mock/real PAN world model ->
risk evaluator. Writes a judge-readable `candidates.json` + `summary.txt` +
per-step rollout assets to `out_dir`.

Failure tolerance (PAN.md sec 12): this never crashes when PAN is
unavailable -- gated/unavailable/failed steps simply carry no assets, and
`candidates.json` is still written. `run_demo(backend="mock")` is the fully
offline path exercised by `tests/test_pan_demo.py`.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from pan.actions import describe_action
from pan.evaluate import compose_side_by_side, evaluate_rollout, save_png
from pan.observation import observation_from_scene, save_observation
from pan.rollouts import RolloutManager
from pan.solver_bridge import candidates_from_packer3d, first_divergence
from pan.types import CandidateSequence, PackingAction, Scene, SimulationResult, apply_action, apply_sequence
from pan.world_model import get_world_model
from tests.fixtures import valid_packed_scene

_LOG = logging.getLogger("pan.demo")

_IDENTITY = (0.0, 0.0, 0.0, 1.0)

LABELS: dict[str, str] = {
    "shoe": "black running shoe",
    "camera": "mirrorless camera",
    "laptop": "13-inch laptop",
    "headphones_case": "headphones case",
    "charger": "charger",
    "toiletry_bottle": "toiletry bottle",
    "toiletry_bag": "toiletry bag",
}


def _fixture_pose(object_id: str) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    for o in valid_packed_scene().objects:
        if o.id == object_id:
            return o.position, o.rotation
    raise KeyError(object_id)


def build_demo_state() -> tuple[Scene, list[CandidateSequence], dict[str, str]]:
    """Current state: `valid_packed_scene()` with `shoe` and `camera` pulled
    onto the table beside the suitcase (their own half-heights above y=0);
    everything else already packed. Three K=3 candidates (PAN.md sec 18):

      A "shoe first":       shoe -> its fixture pose, then camera -> its fixture pose.
      B "camera first":     camera -> its fixture pose, then shoe -> its fixture pose.
      C "camera on laptop": camera placed flat on top of the fragile laptop
                             (`cannot_support_weight=True` -> physics rejects
                             it with FRAGILE_OBJECT_OVERLOADED), then shoe ->
                             its fixture pose.
    """
    scene = valid_packed_scene()
    by_id = {o.id: o for o in scene.objects}
    shoe_dims = by_id["shoe"].dimensions
    camera_dims = by_id["camera"].dimensions
    laptop_pos = by_id["laptop"].position
    laptop_dims = by_id["laptop"].dimensions

    table_positions = {
        "shoe": (-0.75, shoe_dims[1] / 2.0, 0.0),
        "camera": (0.75, camera_dims[1] / 2.0, 0.0),
    }
    objects = [
        replace(o, position=table_positions[o.id]) if o.id in table_positions else o
        for o in scene.objects
    ]
    state = Scene(container=scene.container, objects=objects)

    shoe_pos, shoe_rot = _fixture_pose("shoe")
    camera_pos, camera_rot = _fixture_pose("camera")
    # Flush on top of the laptop's largest face: laptop_top_y + camera_half_y.
    camera_on_laptop = (
        laptop_pos[0],
        laptop_pos[1] + laptop_dims[1] / 2.0 + camera_dims[1] / 2.0,
        laptop_pos[2],
    )

    candidates = [
        CandidateSequence(
            candidate_id="A",
            label="shoe first",
            actions=[
                PackingAction(object_id="shoe", target_position=shoe_pos, target_rotation=shoe_rot, order_index=0),
                PackingAction(object_id="camera", target_position=camera_pos, target_rotation=camera_rot, order_index=1),
            ],
        ),
        CandidateSequence(
            candidate_id="B",
            label="camera first",
            actions=[
                PackingAction(object_id="camera", target_position=camera_pos, target_rotation=camera_rot, order_index=0),
                PackingAction(object_id="shoe", target_position=shoe_pos, target_rotation=shoe_rot, order_index=1),
            ],
        ),
        CandidateSequence(
            candidate_id="C",
            label="camera on laptop",
            actions=[
                PackingAction(object_id="camera", target_position=camera_on_laptop, target_rotation=_IDENTITY, order_index=0),
                PackingAction(object_id="shoe", target_position=shoe_pos, target_rotation=shoe_rot, order_index=1),
            ],
        ),
    ]
    return state, candidates, dict(LABELS)


def build_solver_state(
    result_path: str | Path,
    scenario_path: str | Path,
    *,
    strategy_a: str = "naive",
    strategy_b: str = "optimized",
) -> tuple[Scene, list[CandidateSequence], dict[str, str], str]:
    """Same contract as `build_demo_state()` plus a note line, but built from a
    packer3d result (`pan.solver_bridge`) instead of the hard-coded carry-on.

    The two strategies usually share a prefix of identical placements; those are
    applied to the scene up front so the rollout starts at `first_divergence` -- the
    only step where the candidates actually differ (and the only interesting
    counterfactual). The note records that for `summary.txt`.
    """
    result = json.loads(Path(result_path).read_text())
    scenario = json.loads(Path(scenario_path).read_text())
    state, candidates, labels = candidates_from_packer3d(result, scenario)
    wanted = [s for s in (strategy_a, strategy_b) if s]
    picked = [c for c in candidates if c.candidate_id in wanted] or candidates

    k = first_divergence(picked) or 0
    state = apply_sequence(state, picked[0].actions, upto=k)
    picked = [
        replace(c, actions=[replace(a, order_index=i) for i, a in enumerate(c.actions[k:])])
        for c in picked
    ]
    head = f"steps 1–{k} identical; " if k else "no common prefix; "
    places = ", ".join(f"{c.candidate_id} places {c.actions[0].object_id}" for c in picked if c.actions)
    return state, picked, labels, f"{head}imagining step {k + 1}: {places}"


def persist_result(result: SimulationResult, out_dir: str | Path) -> SimulationResult:
    """Write frame_00.png..frame_NN.png + rollout.gif into `out_dir` (mirrors
    `MockPanBackend.persist`, but works for any backend's result). Returns a
    copy of `result` with `video_path`/`final_frame_path` filled in."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not result.frames:
        return result

    frame_paths = []
    for i, frame in enumerate(result.frames):
        p = out_dir / f"frame_{i:02d}.png"
        Image.fromarray(np.asarray(frame, dtype=np.uint8)).save(p)
        frame_paths.append(p)

    gif_path = out_dir / "rollout.gif"
    images = [Image.fromarray(np.asarray(f, dtype=np.uint8)) for f in result.frames]
    images[0].save(gif_path, save_all=True, append_images=images[1:], duration=150, loop=0)

    return replace(result, video_path=str(gif_path), final_frame_path=str(frame_paths[-1]))


def _relativize(path_str: Optional[str], base: Path) -> Optional[str]:
    if not path_str:
        return path_str
    try:
        return str(Path(path_str).resolve().relative_to(base.resolve()))
    except ValueError:
        return path_str  # not under out_dir; leave as-is rather than guess


def _relativize_reports(dicts: list[dict], base: Path) -> None:
    for d in dicts:
        for key in ("pan_preview_video", "pan_final_frame"):
            d[key] = _relativize(d.get(key), base)


def _write_summary(out_dir: Path, rollups: list, note: Optional[str] = None) -> None:
    """PAN.md sec 20 judge-readable block, exact shape:

        Candidate A  shoe first
          physics: valid   PAN: complete   execution risk: low
          action: ...

    `note` (the solver path's divergence line) is prepended when given.
    """
    lines: list[str] = []
    if note:
        lines += [note, ""]
    for r in rollups:
        lines.append(f"Candidate {r.candidate_id}  {r.label}")
        lines.append(f"  physics: {r.physics_status}   PAN: {r.simulation_status}   execution risk: {r.execution_risk or 'n/a'}")
        lines.append(f"  action: {r.action_text}")
        lines.append("")
    (out_dir / "summary.txt").write_text("\n".join(lines).rstrip() + "\n")


def run_demo(
    out_dir: str | Path,
    *,
    backend: str = "auto",
    steps: int = 2,
    num_frames: int = 8,
    viewpoint: str = "overhead_45",
    timeout_s: float = 120.0,
    from_packer3d: str | Path | None = None,
    scenario: str | Path | None = None,
    strategy_a: str = "naive",
    strategy_b: str = "optimized",
) -> dict:
    """`from_packer3d` (a packer3d result JSON) + `scenario` (its input JSON) swap the
    hard-coded carry-on state for the solver's own candidates via `build_solver_state`;
    without them the default demo path is unchanged."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    note = None
    if from_packer3d is not None:
        if scenario is None:
            raise ValueError("from_packer3d needs the matching scenario= JSON (it lists every item)")
        state, candidates, labels, note = build_solver_state(
            from_packer3d, scenario, strategy_a=strategy_a, strategy_b=strategy_b
        )
    else:
        state, candidates, labels = build_demo_state()

    state_obs = observation_from_scene(state, viewpoint)
    save_observation(state_obs, out_dir / "state_observation.png")

    world_model = get_world_model(backend, cache_dir=out_dir / ".pan_cache")
    manager = RolloutManager(
        world_model,
        observation_fn=lambda s: observation_from_scene(s, viewpoint),
        describe_fn=lambda s, a: describe_action(s, a, labels=labels),
        evaluate_fn=evaluate_rollout,
        options={"num_frames": num_frames},
    )

    t0 = time.monotonic()
    batch = manager.simulate_candidate_actions(state, candidates, steps=steps)
    returned_after_ms = (time.monotonic() - t0) * 1000.0
    _LOG.info(
        "simulate_candidate_actions returned after %.2fms (physics gate resolved "
        "synchronously; any PAN calls continue asynchronously)",
        returned_after_ms,
    )

    batch.wait(timeout_s)

    records_by_candidate: dict[str, list] = {}
    for rec in batch.records:
        records_by_candidate.setdefault(rec.candidate_id, []).append(rec)

    # Persist rollout assets + the "expected" (idealized, physics-target) frame
    # for every completed step, so the renderer can show PLAN vs
    # PAN-PREDICTED EXECUTION (PAN.md Level 1).
    for cand in candidates:
        recs = records_by_candidate.get(cand.candidate_id, [])
        for s, rec in enumerate(recs):
            if rec.status != "complete" or rec.result is None or not rec.result.frames:
                continue
            action = cand.actions[s]
            scene_s = apply_sequence(state, cand.actions, upto=s)
            step_dir = out_dir / "candidates" / cand.candidate_id / f"step_{s}"
            rec.result = persist_result(rec.result, step_dir)
            expected_obs = observation_from_scene(apply_action(scene_s, action), viewpoint)
            save_observation(expected_obs, step_dir / "expected.png")

    # comparison.png: one row per candidate's FIRST step, columns first/last/expected.
    comparison_panels = []
    for cand in candidates:
        recs = records_by_candidate.get(cand.candidate_id, [])
        rec0 = recs[0] if recs else None
        action0 = cand.actions[0]
        expected_img = observation_from_scene(apply_action(state, action0), viewpoint).image
        if rec0 is not None and rec0.status == "complete" and rec0.result is not None and rec0.result.frames:
            first_img, last_img = rec0.result.frames[0], rec0.result.frames[-1]
        else:
            still = observation_from_scene(state, viewpoint).image
            first_img, last_img = still, still
        comparison_panels.append((f"{cand.candidate_id} {cand.label}", [first_img, last_img, expected_img]))
    comparison = compose_side_by_side(comparison_panels, frame_indices=(0, 1, 2))
    save_png(comparison, out_dir / "comparison.png")

    candidate_rollups = batch.candidate_reports()
    candidate_dicts = [r.to_dict() for r in candidate_rollups]
    step_dicts = [r.to_dict() for r in batch.reports()]
    _relativize_reports(candidate_dicts, out_dir)
    _relativize_reports(step_dicts, out_dir)

    payload = {
        "backend": world_model.name,
        "pan_available": world_model.available(),
        "returned_after_ms": returned_after_ms,
        "candidates": candidate_dicts,
        "steps": step_dicts,
    }
    if note:
        payload["divergence"] = note
    (out_dir / "candidates.json").write_text(json.dumps(payload, indent=2))
    _write_summary(out_dir, candidate_rollups, note=note)

    manager.shutdown(wait=False)
    return payload
