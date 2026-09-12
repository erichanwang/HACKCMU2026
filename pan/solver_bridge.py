"""packer3d solver result -> PAN candidate sequences.

One job: turn the teammate's solver output into what `pan.rollouts.RolloutManager`
eats -- an initial `Scene` (everything still on the table, from
`physics.packer3d_adapter.scene_from_packer3d_scenario`) plus one
`CandidateSequence` per strategy, whose actions are that strategy's placements IN
SOLVER ORDER (`order_index` = index in `result["placements"]`).

The frame mapping lives in `physics/packer3d_adapter.py` -- nothing here does geometry.
"""
from __future__ import annotations

from typing import Optional

from physics.packer3d_adapter import placements_from_packer3d, scene_from_packer3d_scenario
from physics.schema import Scene
from pan.types import CandidateSequence, PackingAction

STRATEGY_LABELS = {"naive": "naive first-fit order", "optimized": "optimized order"}


def humanize(object_id: str) -> str:
    """`shoes_1` -> `shoes 1`. The ids are the only semantics we have."""
    return object_id.replace("_", " ")


def _strategies(result: dict) -> list[str]:
    if "placements" in result:
        return [str(result.get("strategy", "unknown"))]
    return [k for k, v in result.items() if isinstance(v, dict) and "placements" in v]


def candidates_from_packer3d(
    compare_or_single: dict, scenario: dict, *, labels: Optional[dict] = None
) -> tuple[Scene, list[CandidateSequence], dict[str, str]]:
    """(initial unpacked scene, one candidate per strategy, labels by object id).

    `compare_or_single` is a packer3d result: either a single-strategy dict or the
    `--compare` wrapper `{"naive": {...}, "optimized": {...}}`. `scenario` is the input
    scenario dict (it, not the result, knows every item -- including the unpacked ones).
    `labels` overrides the id-derived labels for the ids it mentions.
    """
    scene = scene_from_packer3d_scenario(scenario)
    label_map = {o.id: humanize(o.id) for o in scene.objects}
    label_map.update(labels or {})

    single = "placements" in compare_or_single
    candidates = []
    for strategy in _strategies(compare_or_single):
        placements = placements_from_packer3d(compare_or_single, strategy=None if single else strategy)
        candidates.append(
            CandidateSequence(
                candidate_id=strategy,
                label=STRATEGY_LABELS.get(strategy, f"{strategy} order"),
                actions=[
                    PackingAction(
                        object_id=p["id"],
                        target_position=tuple(p["position"]),
                        target_rotation=tuple(p["rotation"]),
                        order_index=i,
                        label=label_map.get(p["id"]),
                        candidate_id=strategy,
                    )
                    for i, p in enumerate(placements)
                ],
            )
        )
    return scene, candidates, label_map


def first_divergence(candidates: list[CandidateSequence]) -> Optional[int]:
    """First step index where the candidates act on different objects (the interesting
    counterfactual step: everything before it is a shared prefix). `None` when the
    sequences agree on every object, in order, for their whole length."""
    if len(candidates) < 2:
        return None
    common = min(len(c.actions) for c in candidates)
    for i in range(common):
        if len({c.actions[i].object_id for c in candidates}) > 1:
            return i
    return common if len({len(c.actions) for c in candidates}) > 1 else None
