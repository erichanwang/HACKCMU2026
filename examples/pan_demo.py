"""Counterfactual packing rollout demo (PAN.md section 13).

Loads the carry-on fixture, pulls "shoe" back out as the next item to place,
and compares two candidate placements through the physics gate and the
(currently mock) PAN world model.

    python3 examples/pan_demo.py
"""
from __future__ import annotations

import json
from pathlib import Path

from physics.io import scene_from_dict
from physics.pan import MockPanBackend, PanAction, RealPanBackend, result_note, simulate_candidate_actions

FIXTURE = Path(__file__).parent / "scene_carry_on.json"


def main() -> None:
    scene = scene_from_dict(json.loads(FIXTURE.read_text()))

    candidates = [
        # keep the shoe where the fixture already has it
        PanAction(object_id="shoe", target_position=(-0.13, 0.055, 0.1), order=1),
        # rotate it 90 degrees into the toiletry bottle's spot instead
        PanAction(object_id="shoe", target_position=(0.18, 0.055, -0.02), target_rotation=(0.0, 0.7071, 0.0, 0.7071), order=1),
    ]

    backend = RealPanBackend()
    if not backend.api_key:
        backend = MockPanBackend()
    results = simulate_candidate_actions(scene, "carry_on_demo", candidates, backend)

    for label, r in zip("AB", results):
        pan = r["pan"]
        print(f"Candidate {label}: {r['action_text']}")
        print(f"  physics: valid={r['physics_valid']} score={r['physics_score']:.2f}")
        print(f"  PAN [{pan.backend}]: status={pan.status} video={pan.video_path} error={pan.error}")
        # Never print a result without saying what it is (neither backend is a visual PAN rollout).
        note = result_note(pan)
        if note:
            print(f"  what this is: {note}")
        if pan.metadata.get("risk"):
            print(f"  risk: {pan.metadata['risk']}")
        print()


if __name__ == "__main__":
    main()
