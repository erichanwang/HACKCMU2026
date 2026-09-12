#!/usr/bin/env python3
"""The live demo, in one process: fixture scan -> server -> solver + physics -> PAN -> iOS plan.

    python3 scripts/demo_e2e.py --server http://127.0.0.1:8000 --out out/e2e

Posts a fixture carry-on and its scanned items to the running server, asks for a
plan (`POST /suitcases/{id}/plan` -> packer3d + `physics.validator` + the app's
plan JSON), then runs the solver's own candidate order through the PAN world
model. Nothing here re-implements the solver, the validator or the rollouts --
it only wires the existing pieces together in demo order.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # `pan` and `physics` live at the repo root

SUITCASE = {"name": "Carry-on", "dimensions": [0.34, 0.20, 0.50]}  # [width, height, depth] m

# Fixture "scans": a realistic carry-on load. `dimensions` is [width, height, depth] in
# metres, matching SCAN_OUTPUT.md; the heightmap is filled in by `scan()`.
# ponytail: flat-top heightmaps (every cell == height, i.e. the full bounding box).
# The solver packs the bbox + compressibility, so a real dip only changes the picture.
FIXTURE_ITEMS = [
    {"name": "book", "dimensions": [0.15, 0.04, 0.22], "label": "hardcover book", "rigidity": "rigid", "compressibility": 1.0},
    {"name": "tshirts", "dimensions": [0.30, 0.10, 0.22], "label": "folded t-shirt stack", "rigidity": "soft", "compressibility": 2.0},
    {"name": "camera", "dimensions": [0.13, 0.09, 0.10], "label": "mirrorless camera", "rigidity": "fragile", "compressibility": 1.0},
    {"name": "bottle", "dimensions": [0.07, 0.18, 0.07], "label": "shampoo bottle (keep upright)", "rigidity": "rigid", "compressibility": 1.0},
    {"name": "shoes", "dimensions": [0.28, 0.11, 0.11], "label": "running shoes", "rigidity": "soft", "compressibility": 1.3},
]
CELL = 0.02  # m; a coarse-but-honest scan grid, keeps the fixture payloads tiny

# ponytail: the PAN rollup dicts (pan.types.CandidateReport) carry no backend name or
# mock mode, so the honesty line is keyed off the backend name here. Delete this map if
# `pan/demo.py` starts writing the note into candidates.json.
BACKEND_NOTES = {
    "mock": "synthetic frames (pan.world_model.MockPanBackend draws the placement; no physics, not a PAN rollout)",
    "pan": "real PAN world model",
}


def die(message: str) -> None:
    print(f"\nerror: {message}", file=sys.stderr)
    raise SystemExit(1)


# --- server ------------------------------------------------------------------


def call(url: str, *, data: bytes | None = None, content_type: str | None = None, method: str = "GET") -> dict:
    headers = {"Content-Type": content_type} if content_type else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        die(f"{method} {url} -> HTTP {e.code}: {e.read().decode()[:400]}")
    except urllib.error.URLError as e:
        die(
            f"cannot reach the server at {url} ({e.reason}). Start Mongo and the server:\n"
            "  docker run --rm -d --name suitcase-mongo -p 27017:27017 mongo:7\n"
            "  cd server && uv run uvicorn main:app --port 8000"
        )


def post_json(url: str, payload: dict) -> dict:
    return call(url, data=json.dumps(payload).encode(), content_type="application/json", method="POST")


def patch_json(url: str, payload: dict) -> dict:
    return call(url, data=json.dumps(payload).encode(), content_type="application/json", method="PATCH")


def post_item(url: str, item: dict, jpeg: bytes) -> dict:
    """`POST /items`: multipart form with the scan JSON in `item` and a photo in `image`."""
    boundary = uuid.uuid4().hex
    body = b"".join(
        [
            f'--{boundary}\r\nContent-Disposition: form-data; name="item"\r\n\r\n'.encode(),
            json.dumps(item).encode(),
            f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="scan.jpg"\r\n'
            f"Content-Type: image/jpeg\r\n\r\n".encode(),
            jpeg,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    return call(url, data=body, content_type=f"multipart/form-data; boundary={boundary}", method="POST")


# --- fixture -----------------------------------------------------------------


def scan(fixture: dict, suitcase_id: str) -> dict:
    """One fixture entry -> the `ScannedItem` JSON the phone would POST (SCAN_OUTPUT.md)."""
    width, height, depth = fixture["dimensions"]
    rows, cols = math.ceil(width / CELL), math.ceil(depth / CELL)
    return {
        "id": f"{fixture['name']}-{uuid.uuid4().hex[:8]}",
        "suitcaseId": suitcase_id,
        "dimensions": [width, height, depth],
        "cellSize": CELL,
        "heights": [[height] * cols for _ in range(rows)],
    }


def jpeg() -> bytes:
    """A 16x16 grey JPEG -- the server only forwards it to Grok, and the demo runs
    without an `XAI_API_KEY` (the fixture's own label/rigidity is PATCHed in below)."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (128, 128, 128)).save(buf, "JPEG")
    return buf.getvalue()


# --- steps -------------------------------------------------------------------


def seed(server: str) -> tuple[dict, list[dict]]:
    suitcase = post_json(f"{server}/suitcases", SUITCASE)
    print(f"[1/4] suitcase {suitcase['id']}  {SUITCASE['name']} {SUITCASE['dimensions']} m")
    photo = jpeg()
    items = []
    for fixture in FIXTURE_ITEMS:
        doc = post_item(f"{server}/items", scan(fixture, suitcase["id"]), photo)
        doc = patch_json(
            f"{server}/items/{doc['id']}",
            {k: fixture[k] for k in ("label", "rigidity", "compressibility")},
        )
        items.append(doc)
        print(f"      + {doc['label']:<30} {fixture['dimensions']} m  {doc['rigidity']}  k={doc['compressibility']}")
    return suitcase, items


def make_plan(server: str, suitcase: dict, items: list[dict], out: Path) -> dict:
    doc = post_json(f"{server}/suitcases/{suitcase['id']}/plan", {})
    solver, validation, metrics = doc["solver"], doc["validation"], doc["solver"]["metrics"]
    (out / "solver.json").write_text(json.dumps(solver, indent=2))
    (out / "validation.json").write_text(json.dumps(validation, indent=2))
    (out / "plan.json").write_text(json.dumps(doc["plan"], indent=2))
    # the packer3d scenario that produced this result (server/planner.py builds the same
    # dict); `pan.demo` needs it to know about the items the solver left out.
    width, height, depth = (float(v) for v in suitcase["dimensions"])
    (out / "scenario.json").write_text(
        json.dumps({"container": {"id": suitcase["id"], "dims": [width, depth, height]}, "items": items}, indent=2)
    )
    print(
        f"[2/4] plan: {metrics['items_packed']} packed / {metrics['items_unpacked']} unpacked, "
        f"volume utilisation {metrics['volume_utilization']:.1%}, "
        f"physics valid={validation['valid']} violations={len(validation['violations'])}"
    )
    return doc


def rollout_mock(out: Path) -> None:
    from pan.demo import run_demo

    payload = run_demo(
        out / "pan",
        backend="mock",
        steps=1,
        num_frames=3,
        from_packer3d=out / "solver.json",
        scenario=out / "scenario.json",
    )
    backend = payload["backend"]
    note = BACKEND_NOTES.get(backend.removeprefix("cache(").removesuffix(")"), backend)  # run_demo wraps it in a cache
    print(f"[3/4] PAN rollouts [{backend}] available={payload['pan_available']} -- {note}")
    for c in payload["candidates"]:
        print(
            f"      candidate {c['candidate_id']} ({c['label']}): physics={c['physics_status']} "
            f"pan={c['simulation_status']} execution risk={c['execution_risk'] or 'n/a'} "
            f"backend={backend} [{note}]"
        )
        print(f"        action: {c['action_text']}")


def rollout_real(doc: dict, items: list[dict], suitcase_id: str) -> None:
    from physics.pan import PanAction, RealPanBackend, simulate_candidate_actions
    from physics.packer3d_adapter import placements_from_packer3d, scene_from_packer3d

    scene, _extras = scene_from_packer3d(doc["solver"], items=items)
    # ponytail: target_position only. `scene_from_packer3d` already bakes the solver's
    # axis permutation into each object's dimensions (identity rotation), so re-applying
    # `placements_from_packer3d`'s rotation would rotate a permuted item a second time and
    # the physics gate rejects it. That pairing belongs to the unpacked-scenario flow.
    actions = [
        PanAction(object_id=p["id"], target_position=tuple(p["position"]), order=i + 1)
        for i, p in enumerate(placements_from_packer3d(doc["solver"])[:2])
    ]
    backend = RealPanBackend()
    print(f"[3/4] PAN rollouts [{backend.name}] on the first {len(actions)} placements")
    for r in simulate_candidate_actions(scene, suitcase_id, actions, backend):
        pan = r["pan"]
        note = pan.metadata.get("note") or pan.error or ""
        print(
            f"      candidate {r['order']} ({r['object_id']}): physics valid={r['physics_valid']} "
            f"score={r['physics_score']:.2f}  pan={pan.status} backend={pan.backend} [{note}]"
        )
        print(f"        action: {r['action_text']}")
        if pan.metadata.get("risk"):
            print(f"        risk: {json.dumps(pan.metadata['risk'])}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--server", default="http://127.0.0.1:8000")
    p.add_argument("--out", default="out/e2e")
    p.add_argument("--backend", default="mock", choices=["mock", "real"])
    p.add_argument("--keep", action="store_true", help="(no-op today: the server has no delete route, data always stays)")
    args = p.parse_args(argv)

    if args.backend == "real":
        key = os.environ.get("IFM_API_KEY") or os.environ.get("PAN_API_KEY")  # Eric's .env names it PAN_API_KEY
        if not key:
            die("--backend real needs IFM_API_KEY (or PAN_API_KEY) in the environment")
        os.environ["IFM_API_KEY"] = key

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    server = args.server.rstrip("/")

    suitcase, items = seed(server)
    doc = make_plan(server, suitcase, items, out)
    if args.backend == "mock":
        rollout_mock(out)
    else:
        rollout_real(doc, items, suitcase["id"])

    plan = doc["plan"]
    print(f"[4/4] iOS plan: {out / 'plan.json'}")
    print(
        f"      {len(plan['placements'])} placements in '{plan['container']['label']}' "
        f"(suitcase {suitcase['id']}); solver.json / validation.json / scenario.json alongside it."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
