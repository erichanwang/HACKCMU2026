"""Render every public scene factory in tests/fixtures.py at both default PAN
viewpoints, so PAN can be exercised without an iPhone or a live scene.

Usage: python3 pan/render_fixtures.py  (writes into pan/fixtures/)
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from pan.observation import DEFAULT_VIEWPOINTS, observation_from_scene, save_observation  # noqa: E402
from tests import fixtures  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_RENDER_SIZE = (384, 384)


def _scene_factories():
    """Public, zero-arg scene_*/valid_packed_scene factories in tests/fixtures.py."""
    for name, fn in inspect.getmembers(fixtures, inspect.isfunction):
        if fn.__module__ != fixtures.__name__ or name.startswith("_"):
            continue
        if name == "valid_packed_scene" or name.startswith("scene_"):
            yield name, fn


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    index: dict[str, dict] = {}
    for scene_name, factory in _scene_factories():
        scene = factory()
        for viewpoint_name in DEFAULT_VIEWPOINTS:
            width, height = _RENDER_SIZE
            obs = observation_from_scene(
                scene, viewpoint_name, scene_id=scene_name, width=width, height=height
            )
            filename = f"{scene_name}_{viewpoint_name}.png"
            save_observation(obs, FIXTURES_DIR / filename)
            index[filename] = {
                "scene": scene_name,
                "viewpoint": viewpoint_name,
                "object_colors": {oid: list(c) for oid, c in obs.metadata["object_colors"].items()},
            }
    (FIXTURES_DIR / "index.json").write_text(json.dumps(index, indent=2, sort_keys=True))
    print(f"wrote {len(index)} fixture images + index.json to {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
