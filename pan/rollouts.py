"""Counterfactual rollout manager (PAN.md sec 4/5/12/17/18/19).

Given the solver's top-K candidate action sequences, run each candidate's
steps through the physics gate and then (async, off the solver's critical
path) through the PAN world model, so the caller gets ranking-hint reports
without ever blocking on PAN latency.

Threading design: physics validation is cheap/deterministic/local, so the
gate + world-model-availability decision for every (candidate, step) is made
synchronously in `simulate_candidate_actions` -- that's what lets it return
"pending" records immediately with no PAN call made yet. Only genuinely
PAN-bound work (rendering the observation and calling `world_model.simulate`)
is submitted to the executor. One worker task per candidate runs that
candidate's PAN-bound steps in order (satisfies "step s+1 waits for step s"
without needing a chained-futures graph); the previous step's `SimulationResult`
is threaded through as `history` when `world_model.supports_continuation`.

Cancellation is cooperative: `RolloutBatch.cancel()` sets an event, tries
`Future.cancel()` on not-yet-started per-candidate tasks, and immediately
marks every still-"pending" record "failed"/"cancelled" under the batch lock.
A worker thread that was already mid-flight for one of those records checks,
after its `simulate()` call returns, whether its record was cancelled out
from under it and -- if so -- discards its result instead of overwriting the
cancellation. We can't kill an OS thread mid network-call, so a cancelled
step's PAN request may still run to completion in the background; its result
is just never attached.
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
import threading
import time
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from typing import Callable, Optional

import numpy as np

from pan.types import (
    CandidateReport,
    CandidateSequence,
    PackingAction,
    RiskSignals,
    RolloutRecord,
    Scene,
    SimulationRequest,
    SimulationStatus,
    WorldModel,
    apply_action,
    apply_sequence,
    honesty_note,
)
from physics.geometry import obb_from, obb_vertices
from physics.validator import validate_layout

_LOG = logging.getLogger("pan.rollouts")

ObservationFn = Callable[[Scene], "pan.types.Observation"]
DescribeFn = Callable[[Scene, PackingAction], str]
EvaluateFn = Callable[["pan.types.SimulationResult", "pan.types.Observation", PackingAction], Optional[RiskSignals]]


def _to_native(value):
    """Recursively convert numpy scalars/arrays inside a dict/list to plain Python types."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {k: _to_native(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_native(v) for v in value]
    return value


def packing_subset(scene: Scene, placed_ids: Optional[set[str]] = None) -> Scene:
    """The part of `scene` the physics gate should judge.

    A partially-packed state legitimately contains items still lying on the
    table beside the suitcase; they are not violations, they are just not
    packed yet. With `placed_ids` given, keep exactly those objects. Otherwise
    infer: keep every object whose world AABB intersects the container's AABB
    (fully-outside objects are "not yet packed"; anything partially inside is
    still judged, so a real wall penetration is never hidden).
    """
    if placed_ids is not None:
        return Scene(scene.container, [o for o in scene.objects if o.id in placed_ids])
    try:
        cv = obb_vertices(obb_from(scene.container))
        verts = [obb_vertices(obb_from(o)) for o in scene.objects]
    except ValueError:
        return scene  # malformed: let validate_layout report it
    c_lo, c_hi = cv.min(axis=0), cv.max(axis=0)
    kept = [o for o, v in zip(scene.objects, verts)
            if bool(np.all(v.max(axis=0) >= c_lo) and np.all(v.min(axis=0) <= c_hi))]
    return Scene(scene.container, kept)


def _request_id(candidate_id: str, step: int, action: PackingAction) -> str:
    payload = f"{candidate_id}:{step}:{action.object_id}:{action.text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


# worst-first priority: a candidate with any step this bad is reported this bad.
_SIM_STATUS_PRIORITY: tuple[SimulationStatus, ...] = ("failed", "pending", "unavailable")
_RISK_RANK = {"high": 3, "medium": 2, "low": 1}


def _worst_simulation_status(statuses: list[SimulationStatus]) -> SimulationStatus:
    for candidate in _SIM_STATUS_PRIORITY:
        if candidate in statuses:
            return candidate
    return "complete"


def _worst_risk_level(levels: list[Optional[str]]) -> Optional[str]:
    present = [lvl for lvl in levels if lvl is not None]
    if not present:
        return None
    return max(present, key=lambda lvl: _RISK_RANK.get(lvl, 0))


class RolloutBatch:
    """Records for one `simulate_candidate_actions` call, plus live status."""

    def __init__(
        self,
        records: list[RolloutRecord],
        steps: list[int],
        n_objects: list[int],
        labels: dict[str, str],
        risk_weight: float = 0.5,
    ) -> None:
        self.records = records
        self._steps = steps
        self._n_objects = n_objects
        self._labels = labels
        self._weight = risk_weight
        self._futures: list[Future] = []
        self._cancelled = threading.Event()
        self._lock = threading.Lock()

    def _attach(self, futures: list[Future]) -> None:
        self._futures = futures

    def done(self) -> bool:
        return all(f.done() for f in self._futures)

    def wait(self, timeout: float | None = None) -> bool:
        if not self._futures:
            return True
        _done, not_done = futures_wait(self._futures, timeout=timeout)
        return not not_done

    def cancel(self) -> int:
        """Cancel not-yet-started work and mark every still-pending record cancelled."""
        self._cancelled.set()
        count = 0
        with self._lock:
            for fut in self._futures:
                fut.cancel()  # no-op if already running/done
            for rec in self.records:
                if rec.status == "pending":
                    rec.status = "failed"
                    rec.error = "cancelled"
                    count += 1
        return count

    def statuses(self) -> dict[str, SimulationStatus]:
        counts = Counter(r.candidate_id for r in self.records)
        out: dict[str, SimulationStatus] = {}
        for rec, step in zip(self.records, self._steps):
            key = rec.candidate_id if counts[rec.candidate_id] == 1 else f"{rec.candidate_id}/{step}"
            out[key] = rec.status
        return out

    def reports(self) -> list[CandidateReport]:
        """PAN.md sec 20 UI/renderer contract. Safe to call at any point;
        records still in flight simply report `simulation_status="pending"`
        with no assets/risk yet."""
        out = []
        for rec, n in zip(self.records, self._n_objects):
            physics = rec.physics
            if physics.get("valid"):
                physics_status = "valid"
            elif any(v.get("type") == "MALFORMED_GEOMETRY" for v in physics.get("violations", [])):
                physics_status = "malformed"
            else:
                physics_status = "invalid"

            result = rec.result
            risk = rec.risk
            risk_metadata = _to_native(dataclasses.asdict(risk)) if risk is not None else None

            geometry_score = float(physics.get("score", 0.0))
            n_unstable = sum(1 for w in physics.get("warnings", []) if w.get("type") == "UNSTABLE_STACK")
            stability_score = 1.0 - n_unstable / max(1, n)
            pan_risk = max(risk.accessibility_risk, risk.occlusion_risk) if risk is not None else None
            weight = float(self._weight)
            total = geometry_score + stability_score
            if pan_risk is not None:
                total -= weight * pan_risk

            out.append(
                CandidateReport(
                    candidate_id=rec.candidate_id,
                    label=self._labels.get(rec.candidate_id, ""),
                    physics_status=physics_status,
                    simulation_status=rec.status,
                    execution_risk=risk.level() if risk is not None else None,
                    action_text=rec.action.text,
                    pan_preview_video=result.video_path if result is not None else None,
                    pan_final_frame=result.final_frame_path if result is not None else None,
                    backend=result.backend if result is not None else None,
                    backend_note=honesty_note(result.backend, result.metadata) if result is not None else None,
                    risk_metadata=risk_metadata,
                    score_components={
                        "geometry_score": geometry_score,
                        "stability_score": stability_score,
                        "pan_risk": pan_risk,
                        "execution_risk_weight": weight,
                        "total": total,
                    },
                )
            )
        return out

    def candidate_reports(self) -> list[CandidateReport]:
        """One `CandidateReport` per candidate, rolled up over its steps
        (PAN.md sec 20 -- the renderer wants one row per candidate, not one
        per (candidate, step)). Aggregation rules, chosen and documented here:

          physics_status: "valid" iff every step is "valid"; else "malformed"
            if any step is "malformed"; else "invalid".
          simulation_status: worst over steps, priority failed > pending >
            unavailable > complete -- any step "failed" makes the candidate
            "failed"; else any "pending" makes it "pending"; else any
            "unavailable" makes it "unavailable"; else "complete".
          execution_risk: worst level (high > medium > low) among steps that
            have one; None if no step has a level.
          action_text: every step's action text joined by " Then: ".
          pan_preview_video / pan_final_frame: from the LAST step (in order)
            that has one -- so a later gated/failed step doesn't blank out an
            earlier completed rollout's assets.
          backend / backend_note: from the LAST step that actually ran a
            rollout (every step shares one world model), so the assets above
            are never shown unlabelled.
          risk_metadata: {"steps": [per-step risk_metadata, in order]}.
          score_components: mean of each per-step component across steps
            (None entries skipped; a key that's None everywhere stays None),
            plus "n_steps".
        """
        per_step = self.reports()
        order: list[str] = []
        grouped: dict[str, list[CandidateReport]] = {}
        for r in per_step:
            if r.candidate_id not in grouped:
                grouped[r.candidate_id] = []
                order.append(r.candidate_id)
            grouped[r.candidate_id].append(r)

        out = []
        for cid in order:
            step_reports = grouped[cid]
            physics_statuses = [s.physics_status for s in step_reports]
            if all(p == "valid" for p in physics_statuses):
                physics_status = "valid"
            elif any(p == "malformed" for p in physics_statuses):
                physics_status = "malformed"
            else:
                physics_status = "invalid"

            video = frame = backend = backend_note = None
            for s in step_reports:
                if s.pan_preview_video is not None:
                    video = s.pan_preview_video
                if s.pan_final_frame is not None:
                    frame = s.pan_final_frame
                if s.backend is not None:
                    backend, backend_note = s.backend, s.backend_note

            keys = {k for s in step_reports for k in s.score_components}
            components: dict[str, float] = {}
            for k in keys:
                values = [s.score_components[k] for s in step_reports if s.score_components.get(k) is not None]
                components[k] = (sum(values) / len(values)) if values else None
            components["n_steps"] = len(step_reports)

            out.append(
                CandidateReport(
                    candidate_id=cid,
                    label=self._labels.get(cid, ""),
                    physics_status=physics_status,
                    simulation_status=_worst_simulation_status([s.simulation_status for s in step_reports]),
                    execution_risk=_worst_risk_level([s.execution_risk for s in step_reports]),
                    action_text=" Then: ".join(s.action_text for s in step_reports),
                    pan_preview_video=video,
                    pan_final_frame=frame,
                    backend=backend,
                    backend_note=backend_note,
                    risk_metadata={"steps": [s.risk_metadata for s in step_reports]},
                    score_components=components,
                )
            )
        return out


def rank_candidates(batch: RolloutBatch) -> list[CandidateReport]:
    """Ranking HINT, not a gate: physics-valid reports first, then by total
    score descending. PAN risk never overrides a physics violation."""
    reports = batch.reports()
    return sorted(
        reports,
        key=lambda r: (r.physics_status != "valid", -r.score_components.get("total", 0.0)),
    )


def rank_candidate_rollups(batch: RolloutBatch) -> list[CandidateReport]:
    """Like `rank_candidates`, but over the per-candidate rollups from
    `RolloutBatch.candidate_reports()` instead of per-step reports."""
    reports = batch.candidate_reports()
    return sorted(
        reports,
        key=lambda r: (r.physics_status != "valid", -(r.score_components.get("total") or 0.0)),
    )


class RolloutManager:
    def __init__(
        self,
        world_model: WorldModel,
        *,
        max_workers: int = 3,
        observation_fn: ObservationFn,
        describe_fn: Optional[DescribeFn] = None,
        evaluate_fn: Optional[EvaluateFn] = None,
        physics_gate: bool = True,
        options: Optional[dict] = None,
    ) -> None:
        self._world_model = world_model
        self._observation_fn = observation_fn
        self._describe_fn = describe_fn
        self._evaluate_fn = evaluate_fn
        self._physics_gate = physics_gate
        self._options = dict(options) if options else {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def simulate_candidate_actions(
        self,
        scene: Scene,
        candidates: list[CandidateSequence],
        *,
        steps: int = 1,
        placed_ids: Optional[set[str]] = None,
    ) -> RolloutBatch:
        """`placed_ids`: objects already packed before this batch (the physics
        gate judges those plus each candidate's acted objects). When None the
        packed set is inferred geometrically -- see `packing_subset`."""
        records: list[RolloutRecord] = []
        step_numbers: list[int] = []
        n_objects: list[int] = []
        labels = {c.candidate_id: c.label for c in candidates}
        pending_by_candidate: dict[str, list[tuple[int, int]]] = {}  # candidate_id -> [(step, record_index)]

        for cand in candidates:
            n_steps = min(steps, len(cand.actions))
            for s in range(n_steps):
                scene_s = apply_sequence(scene, cand.actions, upto=s)
                action = cand.actions[s]
                if self._describe_fn is not None and not action.text:
                    action.text = self._describe_fn(scene_s, action)

                after = apply_action(scene_s, action)
                placed_s = None
                if placed_ids is not None:
                    placed_s = set(placed_ids) | {a.object_id for a in cand.actions[: s + 1]}
                gate_scene = packing_subset(after, placed_s)
                physics = validate_layout(gate_scene)
                physics_valid = bool(physics["valid"])

                if self._physics_gate and not physics_valid:
                    rec = RolloutRecord(
                        candidate_id=cand.candidate_id,
                        action=action,
                        physics=physics,
                        physics_valid=physics_valid,
                        status="unavailable",
                        error="skipped: physics invalid",
                    )
                elif not self._world_model.available():
                    rec = RolloutRecord(
                        candidate_id=cand.candidate_id,
                        action=action,
                        physics=physics,
                        physics_valid=physics_valid,
                        status="unavailable",
                        error="world model unavailable",
                    )
                else:
                    rec = RolloutRecord(
                        candidate_id=cand.candidate_id,
                        action=action,
                        physics=physics,
                        physics_valid=physics_valid,
                        status="pending",
                    )
                    pending_by_candidate.setdefault(cand.candidate_id, []).append((s, len(records)))

                records.append(rec)
                step_numbers.append(s)
                n_objects.append(max(1, len(gate_scene.objects)))

        batch = RolloutBatch(
            records, step_numbers, n_objects, labels,
            risk_weight=self._options.get("execution_risk_weight", 0.5),
        )

        futures = []
        for cand in candidates:
            steps_and_indices = pending_by_candidate.get(cand.candidate_id)
            if not steps_and_indices:
                continue
            fut = self._executor.submit(self._run_candidate, scene, cand, steps_and_indices, batch)
            futures.append(fut)
        batch._attach(futures)  # noqa: SLF001
        return batch

    def _run_candidate(
        self,
        scene: Scene,
        cand: CandidateSequence,
        steps_and_indices: list[tuple[int, int]],
        batch: RolloutBatch,
    ) -> None:
        prev_result = None
        for step, idx in steps_and_indices:
            if batch._cancelled.is_set():  # noqa: SLF001
                break
            rec = batch.records[idx]
            scene_s = apply_sequence(scene, cand.actions, upto=step)
            action = rec.action

            observation = self._observation_fn(scene_s)
            # ponytail: if step-1 was gated/failed there's no result to continue
            # from; fall back to a fresh (history-less) render rather than
            # threading through a stale, non-adjacent result.
            history = [prev_result] if (self._world_model.supports_continuation and prev_result is not None) else []
            hints = [w.get("type") for w in rec.physics.get("warnings", []) if w.get("object") == action.object_id]
            request = SimulationRequest(
                observation=observation,
                action=action,
                history=history,
                options={**self._options, "physics_hints": hints},
                request_id=_request_id(cand.candidate_id, step, action),
            )

            t0 = time.monotonic()
            try:
                result = self._world_model.simulate(request)
                latency_ms = (time.monotonic() - t0) * 1000.0
                risk = self._evaluate_fn(result, observation, action) if self._evaluate_fn else None
                with batch._lock:  # noqa: SLF001
                    if rec.error == "cancelled":
                        pass  # cancelled out from under us while simulate() was in flight; discard
                    else:
                        rec.result = result
                        rec.status = result.status
                        rec.risk = risk
                _LOG.info(
                    "candidate=%s step=%d status=%s latency_ms=%.1f",
                    cand.candidate_id, step, result.status, latency_ms,
                )
                prev_result = result
            except Exception as exc:  # noqa: BLE001
                latency_ms = (time.monotonic() - t0) * 1000.0
                with batch._lock:  # noqa: SLF001
                    if rec.error != "cancelled":
                        rec.status = "failed"
                        rec.error = str(exc)[:200]
                _LOG.info(
                    "candidate=%s step=%d status=failed latency_ms=%.1f",
                    cand.candidate_id, step, latency_ms,
                )
                prev_result = None

    def shutdown(self, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait)
