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
import time
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
    {"name": "book", "dimensions": [0.15, 0.04, 0.22], "label": "hardcover book", "rigidity": "rigid", "compressibility": 1.0, "mass": 0.6, "keepUpright": False},
    {"name": "tshirts", "dimensions": [0.30, 0.10, 0.22], "label": "folded t-shirt stack", "rigidity": "soft", "compressibility": 2.0, "mass": 0.8, "keepUpright": False},
    {"name": "camera", "dimensions": [0.13, 0.09, 0.10], "label": "mirrorless camera", "rigidity": "fragile", "compressibility": 1.0, "mass": 0.7, "keepUpright": False},
    {"name": "bottle", "dimensions": [0.07, 0.18, 0.07], "label": "shampoo bottle (keep upright)", "rigidity": "rigid", "compressibility": 1.0, "mass": 0.4, "keepUpright": True},
    {"name": "shoes", "dimensions": [0.28, 0.11, 0.11], "label": "running shoes", "rigidity": "soft", "compressibility": 1.3, "mass": 0.9, "keepUpright": False},
]
PATCH_FIELDS = ("label", "rigidity", "compressibility", "mass", "keepUpright")  # what Grok would have guessed
CELL = 0.02  # m; a coarse-but-honest scan grid, keeps the fixture payloads tiny


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


def jpeg(fixture: dict, photos: Path | None) -> bytes:
    """`<photos>/<name>.jpg` when given (Grok labels the real object); otherwise a 16x16
    grey JPEG -- the server only forwards it to Grok, and without `--photos` the fixture's
    own label/rigidity/... is PATCHed in afterwards."""
    if photos is not None:
        return (photos / f"{fixture['name']}.jpg").read_bytes()
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (128, 128, 128)).save(buf, "JPEG")
    return buf.getvalue()


# --- steps -------------------------------------------------------------------


def seed(server: str, photos: Path | None, is_async: bool) -> tuple[dict, list[dict]]:
    suitcase = post_json(f"{server}/suitcases", SUITCASE)
    print(f"[1/4] suitcase {suitcase['id']}  {SUITCASE['name']} {SUITCASE['dimensions']} m"
          + ("  -- photos labelled by Grok on the server" if photos else "  -- fixture labels, no Grok"))
    items = []
    for fixture in FIXTURE_ITEMS:
        url = f"{server}/items?async=1" if is_async else f"{server}/items"
        doc = post_item(url, scan(fixture, suitcase["id"]), jpeg(fixture, photos))
        if is_async:
            print(f"      + {fixture['name']}: posted, labelStatus={doc['labelStatus']}")
            start = time.monotonic()
            while doc["labelStatus"] == "pending":
                if time.monotonic() - start > 60:
                    die(f"item {doc['id']} still labelStatus=pending after 60s")
                time.sleep(2)
                doc = call(f"{server}/items/{doc['id']}")
            print(f"        labelled {doc['label']!r} after {time.monotonic() - start:.1f}s")
        if photos is None:  # no photo worth labelling: restore what Grok would have guessed
            doc = patch_json(f"{server}/items/{doc['id']}", {k: fixture[k] for k in PATCH_FIELDS})
        elif doc["label"] == "unknown":
            die("the server labelled the photo 'unknown': it is running without XAI_API_KEY "
                "(start it with `uv run --env-file ../.env uvicorn main:app`)")
        items.append(doc)
        print(f"      + {doc['label']:<30} {fixture['dimensions']} m  {doc['rigidity']}  k={doc['compressibility']}"
              f"  {doc['mass']} kg{'  keep upright' if doc['keepUpright'] else ''}  [{doc['labelSource']}]")
        if photos is not None and doc.get("description"):
            print(f"        {doc['description']}")
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
    unpacked = doc.get("unpacked")
    if unpacked:
        print(f"      left out: {', '.join(u['label'] for u in unpacked)}")
    pending = doc.get("pendingLabels")
    if pending:
        print(f"      {pending} item(s) still labelling")
    chosen = doc["chosen"]
    print(f"      physics picked {chosen['strategy']}"
          f"{'' if chosen['seed'] is None else ' seed ' + str(chosen['seed'])} of {len(doc['alternatives'])} candidates: "
          + ", ".join(f"{a['strategy']}{'' if a['seed'] is None else a['seed']}="
                      f"{a['items_packed']}pk/{a['volume_utilization']:.0%}/{'ok' if a['physics_valid'] else 'X'}"
                      for a in doc["alternatives"]))
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
    print(f"[3/4] PAN rollouts [{payload['backend']}] available={payload['pan_available']} -- {payload['backend_note']}")
    for c in payload["candidates"]:
        print(
            f"      candidate {c['candidate_id']} ({c['label']}): physics={c['physics_status']} "
            f"pan={c['simulation_status']} execution risk={c['execution_risk'] or 'n/a'} "
            f"backend={c['backend'] or 'none'} [{c['backend_note'] or 'no rollout ran'}]"
        )
        print(f"        action: {c['action_text']}")


def rollout_real(doc: dict, items: list[dict], suitcase_id: str) -> None:
    from physics.pan import PanAction, RealPanBackend, simulate_candidate_actions
    from physics.packer3d_adapter import placements_from_packer3d, scene_from_packer3d

    # oriented=False: the objects keep their own dims and take the solver's axis
    # permutation from the placement's rotation, applied once (see the adapter docstring).
    scene, _extras = scene_from_packer3d(doc["solver"], items=items, oriented=False)
    actions = [
        PanAction(
            object_id=p["id"],
            target_position=tuple(p["position"]),
            target_rotation=tuple(p["rotation"]),
            order=i + 1,
        )
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
    p.add_argument("--photos", type=Path, default=None,
                   help="directory with <book|tshirts|camera|bottle|shoes>.jpg: post real photos and keep "
                        "Grok's label/rigidity/compressibility (server needs XAI_API_KEY)")
    p.add_argument("--keep", action="store_true",
                   help="leave the demo suitcase on the server (default: DELETE /suitcases/{id} at the "
                        "end, which drops its items and stored plan too)")
    p.add_argument("--async", action="store_true", dest="use_async",
                   help="POST items with ?async=1 and poll GET /items/{id} for the label instead of "
                        "waiting on the POST (with --photos this shows Grok labelling in the background)")
    args = p.parse_args(argv)
    if args.photos is not None:
        missing = [f["name"] for f in FIXTURE_ITEMS if not (args.photos / f"{f['name']}.jpg").is_file()]
        if missing:
            die(f"--photos {args.photos}: missing {', '.join(n + '.jpg' for n in missing)}")

    if args.backend == "real":
        key = os.environ.get("IFM_API_KEY") or os.environ.get("PAN_API_KEY")  # Eric's .env names it PAN_API_KEY
        if not key:
            die("--backend real needs IFM_API_KEY (or PAN_API_KEY) in the environment")
        os.environ["IFM_API_KEY"] = key

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    server = args.server.rstrip("/")

    suitcase, items = seed(server, args.photos, args.use_async)
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
    if args.keep:
        print(f"      kept on the server: suitcase {suitcase['id']}, its items and its plan (--keep)")
    else:
        call(f"{server}/suitcases/{suitcase['id']}", method="DELETE")
        print(f"      deleted suitcase {suitcase['id']} and its items from the server (--keep to keep it)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
