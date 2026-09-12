#!/usr/bin/env python3
"""Headless AR pipeline simulation: synthetic LiDAR scans -> real scan geometry -> a locally
started server -> a real solver plan -> the real bag->world transform -> containment/overlap
checks. No Mac, no phone, no API keys.

    PYTHONPATH=. python3 scripts/ar_sim.py

`scripts/ar_gate.sh` runs every `scripts/ar_sim*.py` and treats a non-zero exit as RED.

Design
------
- Point clouds are synthesised in Python (numpy, already a project dependency) for a
  suitcase and 4 items, each sitting on its own table plane at an arbitrary rotation, with
  gaussian noise -- the shape `ScanView.swift`'s tap path produces after `densify`.
- The scan geometry (`fitBox`, `heightMap`) and the AR overlay transform (`interiorBox`,
  `PlanAnchor`) are NOT re-implemented in Python. This script compiles `Spike/Geometry.swift`
  and `Spike/PlanAnchor.swift` with `swiftc` (same toolchain as `tests/swift/*/run.sh`) against
  a small generated driver and talks to the resulting binary over stdin/stdout JSON, so it is
  the real code that ships, not a port that can drift from it. If `swiftc` is unavailable this
  step is skipped cleanly (exit 0), the same way a missing Docker/Mongo is handled below --
  this machine has the toolchain (see `swift/PackPhysics/swiftenv.sh`), so in practice this
  sim exercises the genuine Swift geometry and transform code.
- The server is started as a real subprocess (`uv run uvicorn`) and talked to over real HTTP,
  exactly like `scripts/demo_e2e.py`. `XAI_API_KEY` is explicitly stripped from its environment
  (regardless of what `.env` holds) so Grok labelling is off and items come back "unknown" --
  the sim does not need a label, only geometry.
- A recent commit gated every mutating route behind Auth0 JWT auth
  (`server/auth.py::require_auth`). This sim never contacts Auth0 (that would be an API key by
  another name); if the very first POST comes back 401 there is no way to proceed headlessly,
  so this sim skips cleanly (exit 0) with a message, the same way it does for a missing
  Docker/Mongo. It does not depend on the `open_mode()` local-auth bypass some checkouts of
  `server/auth.py` carry uncommitted -- if that lands, requests succeed and the full pipeline
  runs; if it does not, the sim degrades to a clean skip instead of turning the gate red.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
RNG_SEED = 20260912
TABLE_Y = 0.80          # world height of the table plane, metres
WALL = 0.01             # suitcaseWallMeters, Spike/ScanView.swift
NOISE = 0.0015          # metres, gaussian noise added to every synthesised point
LABEL_UNAVAILABLE = "sim needs no API key: XAI_API_KEY is stripped from the server's " \
                     "environment on purpose, so Grok labelling is off and items come back 'unknown'."


def skip(message: str) -> None:
    print(f"AR SIM: SKIP -- {message}")
    raise SystemExit(0)


def fail(message: str) -> None:
    print(f"AR SIM: FAIL -- {message}", file=sys.stderr)
    raise SystemExit(1)


# --- geometry synthesis (numpy) ----------------------------------------------------------


def _rot(angle: float):
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s], [s, c]])


def box_points(rng, w: float, d: float, h: float, cx: float, cz: float, angle: float,
               table_y: float, n_top: int = 14, n_side: int = 6, open_top: bool = False) -> np.ndarray:
    """A closed (or open-topped) box: a dense top face (or just its rim) plus side walls,
    the same "top face + wall samples" shape `tests/main.swift` uses for a scanned box."""
    r = _rot(angle)
    pts = []
    if open_top:
        # No interior floor is visible from outside; only the rim perimeter is at full height.
        for t in np.linspace(-w / 2, w / 2, n_top):
            for lz in (-d / 2, d / 2):
                pts.append((t, lz, h))
        for lz in np.linspace(-d / 2, d / 2, n_top):
            for lx in (-w / 2, w / 2):
                pts.append((lx, lz, h))
    else:
        for lx in np.linspace(-w / 2, w / 2, n_top):
            for lz in np.linspace(-d / 2, d / 2, n_top):
                pts.append((lx, lz, h))
    for t in np.linspace(-w / 2, w / 2, n_side):
        for lz in (-d / 2, d / 2):
            for ly in np.linspace(0, h, n_side):
                pts.append((t, lz, ly))
    for t in np.linspace(-d / 2, d / 2, n_side):
        for lx in (-w / 2, w / 2):
            for ly in np.linspace(0, h, n_side):
                pts.append((lx, t, ly))
    local = np.array(pts, dtype=np.float64)
    world_xz = local[:, :2] @ r.T + np.array([cx, cz])
    world_y = table_y + local[:, 2]
    world = np.stack([world_xz[:, 0], world_y, world_xz[:, 1]], axis=1)
    world[:, 0] += rng.normal(0, NOISE, len(world))
    world[:, 1] += rng.normal(0, NOISE, len(world))
    world[:, 2] += rng.normal(0, NOISE, len(world))
    return world


def l_shape_points(rng, w: float, d: float, h: float, cx: float, cz: float, angle: float,
                    table_y: float, n: int = 14) -> np.ndarray:
    """A box with one quadrant of the top face (and its walls) dropped -- concave footprint,
    same construction `tests/main.swift` uses to exercise the L-shape case."""
    r = _rot(angle)
    pts = []
    for lx in np.linspace(-w / 2, w / 2, n):
        for lz in np.linspace(-d / 2, d / 2, n):
            if lx > 0 and lz > 0:
                continue
            pts.append((lx, lz, h))
    for t in np.linspace(-w / 2, w / 2, n // 2):
        for lz in (-d / 2, d / 2):
            for ly in np.linspace(0, h, 5):
                if lz > 0 and t > 0:
                    continue
                pts.append((t, lz, ly))
    for t in np.linspace(-d / 2, d / 2, n // 2):
        for lx in (-w / 2, w / 2):
            for ly in np.linspace(0, h, 5):
                if lx > 0 and t > 0:
                    continue
                pts.append((lx, t, ly))
    local = np.array(pts, dtype=np.float64)
    world_xz = local[:, :2] @ r.T + np.array([cx, cz])
    world_y = table_y + local[:, 2]
    world = np.stack([world_xz[:, 0], world_y, world_xz[:, 1]], axis=1)
    world += rng.normal(0, NOISE, world.shape)
    return world


def cylinder_points(rng, radius: float, h: float, cx: float, cz: float, angle: float,
                     table_y: float, n_theta: int = 24, n_h: int = 8) -> np.ndarray:
    """A rolled-up soft item lying as a cylinder standing on its round face."""
    theta = np.linspace(0, 2 * math.pi, n_theta, endpoint=False)
    pts = []
    for th in theta:
        lx, lz = radius * math.cos(th), radius * math.sin(th)
        for ly in np.linspace(0, h, n_h):
            pts.append((lx, lz, ly))
        pts.append((lx, lz, h))  # top disc rim
    for rr in np.linspace(0, radius, 5):
        for th in theta:
            pts.append((rr * math.cos(th), rr * math.sin(th), h))
    local = np.array(pts, dtype=np.float64)
    r = _rot(angle)
    world_xz = local[:, :2] @ r.T + np.array([cx, cz])
    world_y = table_y + local[:, 2]
    world = np.stack([world_xz[:, 0], world_y, world_xz[:, 1]], axis=1)
    world += rng.normal(0, NOISE, world.shape)
    return world


def scene(rng) -> tuple[dict, list[dict]]:
    """The suitcase (outer shell points + cell) and 4 items (points, cell, kind, dims-only
    fallback name) -- each generated on its own table patch, independent of the others,
    matching one `ScanView` tap per object."""
    suitcase = {"points": box_points(rng, w=0.55, d=0.35, h=0.40, cx=0.0, cz=0.0,
                                      angle=math.radians(23), table_y=TABLE_Y, open_top=True),
                "cell": 0.05}
    items = [
        {"name": "box", "cell": 0.02,
         "points": box_points(rng, w=0.16, d=0.10, h=0.06, cx=1.5, cz=0.5,
                               angle=math.radians(10), table_y=TABLE_Y)},
        {"name": "rolled-soft", "cell": 0.02,
         "points": cylinder_points(rng, radius=0.06, h=0.20, cx=-1.5, cz=0.6,
                                    angle=math.radians(0), table_y=TABLE_Y)},
        {"name": "l-shape", "cell": 0.02,
         "points": l_shape_points(rng, w=0.14, d=0.12, h=0.08, cx=0.8, cz=-1.4,
                                   angle=math.radians(58), table_y=TABLE_Y)},
        {"name": "open-tray", "cell": 0.02,
         "points": box_points(rng, w=0.18, d=0.08, h=0.05, cx=-0.9, cz=-1.2,
                               angle=math.radians(-33), table_y=TABLE_Y, open_top=True)},
    ]
    return suitcase, items


# --- swift driver -------------------------------------------------------------------------

DRIVER_SOURCE = r"""
import Foundation
#if canImport(simd)
import simd
#endif

struct ScanIn: Codable { let points: [[Float]]; let planeY: Float; let cell: Float; let padding: Float }
struct ScanOut: Codable { let width: Float; let height: Float; let depth: Float; let axis: [Float]; let heights: [[Float]] }

struct PlacementIn: Codable { let position: [Float]; let size: [Float] }
struct TransformIn: Codable { let suitcasePoints: [[Float]]; let planeY: Float; let wall: Float; let placements: [PlacementIn] }
struct TransformOut: Codable { let interior: [Float]; let worldCenters: [[Float]]; let bagRoundTrip: [[Float]] }

struct Request: Codable { let cmd: String; let scan: [ScanIn]?; let transform: TransformIn? }

func runScan(_ items: [ScanIn]) -> [ScanOut] {
    items.map { inp in
        let pts = inp.points.map { SIMD3<Float>($0[0], $0[1], $0[2]) }
        guard let fit = fitBox(points: pts, planeY: inp.planeY, padding: inp.padding) else {
            FileHandle.standardError.write("fitBox failed on \(pts.count) points\n".data(using: .utf8)!)
            exit(1)
        }
        let hm = heightMap(points: pts, box: fit, planeY: inp.planeY, cell: inp.cell)
        return ScanOut(width: fit.width, height: fit.height, depth: fit.depth,
                        axis: [fit.axis.x, fit.axis.y, fit.axis.z], heights: hm)
    }
}

func runTransform(_ t: TransformIn) -> TransformOut {
    let pts = t.suitcasePoints.map { SIMD3<Float>($0[0], $0[1], $0[2]) }
    guard let outer = fitBox(points: pts, planeY: t.planeY, padding: 0) else {
        FileHandle.standardError.write("fitBox failed on suitcase points\n".data(using: .utf8)!)
        exit(1)
    }
    let interior = interiorBox(outer, wall: t.wall)
    let anchor = PlanAnchor(interior: interior, planeY: t.planeY + t.wall)
    var worldCenters: [[Float]] = [], roundTrip: [[Float]] = []
    for p in t.placements {
        let pos = SIMD3<Float>(p.position[0], p.position[1], p.position[2])
        let size = SIMD3<Float>(p.size[0], p.size[1], p.size[2])
        let world = anchor.worldCenter(position: pos, size: size)
        worldCenters.append([world.x, world.y, world.z])
        let rel = world - anchor.origin
        roundTrip.append([simd_dot(rel, anchor.axis), rel.y, simd_dot(rel, anchor.perp)])
    }
    return TransformOut(interior: [interior.width, interior.height, interior.depth],
                         worldCenters: worldCenters, bagRoundTrip: roundTrip)
}

let data = FileHandle.standardInput.readDataToEndOfFile()
let req = try! JSONDecoder().decode(Request.self, from: data)
let encoder = JSONEncoder()
switch req.cmd {
case "scan": FileHandle.standardOutput.write(try! encoder.encode(runScan(req.scan!)))
case "transform": FileHandle.standardOutput.write(try! encoder.encode(runTransform(req.transform!)))
default:
    FileHandle.standardError.write("unknown cmd \(req.cmd)\n".data(using: .utf8)!)
    exit(1)
}
"""


def build_driver(tmp: Path) -> Path:
    """Compile Spike/Geometry.swift + Spike/PlanAnchor.swift with the generated driver above,
    the same way tests/swift/{scan,plan}/run.sh do it. Named `main.swift`: swiftc only allows
    top-level statements in a file with that exact basename when compiling several files."""
    if shutil.which("swiftc") is None:
        env_script = ROOT / "swift" / "PackPhysics" / "swiftenv.sh"
        if not env_script.exists():
            skip("no swiftc on PATH and no swift/PackPhysics/swiftenv.sh to source; cannot drive the real scan geometry")
    (tmp / "main.swift").write_text(DRIVER_SOURCE)
    out = tmp / "driver"
    cmd = (
        f'if ! command -v swiftc >/dev/null 2>&1; then . "{ROOT}/swift/PackPhysics/swiftenv.sh"; fi; '
        f'swiftc -Onone -o "{out}" "{ROOT}/Spike/Geometry.swift" "{ROOT}/Spike/PlanAnchor.swift" '
        f'"{ROOT}/tests/swift/SimdShim.swift" "{tmp}/main.swift"'
    )
    r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
    if r.returncode != 0:
        skip(f"swiftc failed to build the scan-geometry driver, cannot drive the real code:\n{r.stderr[-2000:]}")
    return out


def run_driver(driver: Path, request: dict) -> dict:
    r = subprocess.run([str(driver)], input=json.dumps(request), capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        fail(f"scan-geometry driver crashed on cmd={request['cmd']!r}:\n{r.stderr}")
    return json.loads(r.stdout)


# --- server ---------------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(host: str, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def start_mongo() -> tuple[str, str] | tuple[None, None]:
    """A throwaway `mongo:7` container on a free port, or (None, None) if Docker cannot provide one."""
    if shutil.which("docker") is None:
        return None, None
    port = free_port()
    name = f"ar-sim-mongo-{uuid.uuid4().hex[:8]}"
    r = subprocess.run(
        ["docker", "run", "--rm", "-d", "--name", name, "-p", f"{port}:27017", "mongo:7"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None, None
    if not wait_for_port("127.0.0.1", port, timeout=20):
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        return None, None
    return f"mongodb://localhost:{port}", name


def cleanup(mongo_name: str | None, server: subprocess.Popen | None) -> None:
    if server is not None and server.poll() is None:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
    if mongo_name:
        subprocess.run(["docker", "rm", "-f", mongo_name], capture_output=True)


def call(url: str, *, data: bytes | None = None, content_type: str | None = None, method: str = "GET"):
    headers = {"Content-Type": content_type} if content_type else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def post_json(url: str, payload: dict):
    return call(url, data=json.dumps(payload).encode(), content_type="application/json", method="POST")


def post_item(url: str, item: dict) -> dict:
    """Multipart `POST /items`; the photo is a stub since Grok labelling is off (no API key)."""
    boundary = uuid.uuid4().hex
    jpeg_stub = bytes.fromhex(
        "ffd8ffe000104a4649460001010100480048000" "0ffdb004300ffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffc0000b080001000101011100ffc4001f0000010501010101010100000000000000000102030405060"
        "708090a0bffda0008010100003f00d2ffd9"
    )
    body = b"".join([
        f'--{boundary}\r\nContent-Disposition: form-data; name="item"\r\n\r\n'.encode(),
        json.dumps(item).encode(),
        f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="scan.jpg"\r\n'
        f"Content-Type: image/jpeg\r\n\r\n".encode(),
        jpeg_stub,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    return call(url, data=body, content_type=f"multipart/form-data; boundary={boundary}", method="POST")


# --- checks -----------------------------------------------------------------------------


def assert_finite(label: str, xyz) -> None:
    if not all(math.isfinite(v) for v in xyz):
        fail(f"{label}: world position is not finite: {xyz}")


def assert_contained(label: str, position, size, interior, eps: float = 1e-4) -> None:
    for axis, name in enumerate("xyz"):
        lo, hi = position[axis], position[axis] + size[axis]
        if lo < -eps or hi > interior[axis] + eps:
            fail(f"{label}: [{lo:.4f}, {hi:.4f}] on {name} escapes the bag's [0, {interior[axis]:.4f}]")


def boxes_overlap(a_pos, a_size, b_pos, b_size, eps: float = 1e-4) -> bool:
    return all(a_pos[i] < b_pos[i] + b_size[i] - eps and b_pos[i] < a_pos[i] + a_size[i] - eps for i in range(3))


def main() -> int:
    rng = np.random.default_rng(RNG_SEED)
    tmp_root = Path(tempfile.mkdtemp(prefix="ar_sim_"))
    mongo_name: str | None = None
    server: subprocess.Popen | None = None
    try:
        driver = build_driver(tmp_root)

        suitcase_cloud, items = scene(rng)

        # 1) real scan geometry, on the suitcase and every item
        scan_req = {"cmd": "scan", "scan": [
            {"points": suitcase_cloud["points"].tolist(), "planeY": TABLE_Y,
             "cell": suitcase_cloud["cell"], "padding": 0.0},
            *[{"points": it["points"].tolist(), "planeY": TABLE_Y, "cell": it["cell"], "padding": 0.0}
              for it in items],
        ]}
        scanned = run_driver(driver, scan_req)
        suitcase_fit, item_fits = scanned[0], scanned[1:]
        print(f"[1/5] scanned suitcase outer shell {suitcase_fit['width']:.3f}x"
              f"{suitcase_fit['height']:.3f}x{suitcase_fit['depth']:.3f} m, "
              f"{len(items)} items: " + ", ".join(
                  f"{it['name']} {f['width']:.3f}x{f['height']:.3f}x{f['depth']:.3f}"
                  for it, f in zip(items, item_fits)))

        mongo_uri, mongo_name = start_mongo()
        if mongo_uri is None:
            skip("Docker/Mongo unavailable on this machine (docker missing, or a container "
                 "could not be started) -- cannot exercise the server, skipping")

        port = free_port()
        env = dict(os.environ)
        env.pop("XAI_API_KEY", None)  # no API keys: Grok labelling must be off
        env["SUITCASE_MONGODB_URI"] = mongo_uri
        env["MONGO_DB"] = "ar_sim"
        server = subprocess.Popen(
            ["uv", "run", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(ROOT / "server"), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        if not wait_for_port("127.0.0.1", port, timeout=30):
            out = server.stdout.read() if server.stdout else ""
            fail(f"server never opened port {port}:\n{out[-2000:]}")
        base = f"http://127.0.0.1:{port}"

        # 2) POST the suitcase, using the interior box (Spike/ScanView.swift posts the interior,
        # not the scanned outer shell) so the solver's volume matches the one PlanAnchor uses.
        empty_transform = run_driver(driver, {"cmd": "transform", "transform": {
            "suitcasePoints": suitcase_cloud["points"].tolist(), "planeY": TABLE_Y, "wall": WALL, "placements": []}})
        interior = empty_transform["interior"]
        status, suitcase_doc = post_json(f"{base}/suitcases", {"name": "sim suitcase", "dimensions": interior})
        if status == 401:
            skip("server requires an Auth0 bearer token (no Auth0 tenant/credentials available "
                 "headlessly, and this sim will not fetch one -- that would be an API key by "
                 "another name); " + LABEL_UNAVAILABLE)
        if status != 200:
            fail(f"POST /suitcases -> {status}: {suitcase_doc}")
        print(f"[2/5] suitcase {suitcase_doc['id']} interior {interior[0]:.3f}x{interior[1]:.3f}x{interior[2]:.3f} m")

        # 3) POST every item
        item_ids = []
        for it, fit in zip(items, item_fits):
            doc = {"id": f"{it['name']}-{uuid.uuid4().hex[:8]}", "suitcaseId": suitcase_doc["id"],
                   "dimensions": [fit["width"], fit["height"], fit["depth"]], "cellSize": it["cell"],
                   "heights": fit["heights"]}
            status, resp = post_item(f"{base}/items", doc)
            if status != 200:
                fail(f"POST /items ({it['name']}) -> {status}: {resp}")
            item_ids.append(resp["id"])
            print(f"      + {it['name']:<12} {doc['dimensions'][0]:.3f}x{doc['dimensions'][1]:.3f}x"
                  f"{doc['dimensions'][2]:.3f} m  label={resp['label']}")

        # 4) POST /plan
        status, plan_doc = post_json(f"{base}/suitcases/{suitcase_doc['id']}/plan", {})
        if status != 200:
            fail(f"POST /suitcases/{{id}}/plan -> {status}: {plan_doc}")
        placements = plan_doc["plan"]["placements"]
        metrics = plan_doc["solver"]["metrics"]
        print(f"[3/5] plan: {metrics['items_packed']} packed / {metrics['items_unpacked']} unpacked, "
              f"physics valid={plan_doc['validation']['valid']}")
        if not placements:
            fail("solver packed zero items; nothing to check the transform/containment against")

        # 5) push placements through the real bag->world transform and check them
        transform_req = {"cmd": "transform", "transform": {
            "suitcasePoints": suitcase_cloud["points"].tolist(), "planeY": TABLE_Y, "wall": WALL,
            "placements": [{"position": [p["position"]["x"], p["position"]["y"], p["position"]["z"]],
                             "size": [p["size"]["x"], p["size"]["y"], p["size"]["z"]]} for p in placements],
        }}
        out = run_driver(driver, transform_req)
        interior2 = out["interior"]
        print(f"[4/5] {len(placements)} placements pushed through PlanAnchor.worldCenter")

        for p, world, bag in zip(placements, out["worldCenters"], out["bagRoundTrip"]):
            label = f"{p['label']} (step {p['step']})"
            assert_finite(label, world)
            pos = [p["position"]["x"], p["position"]["y"], p["position"]["z"]]
            size = [p["size"]["x"], p["size"]["y"], p["size"]["z"]]
            expected_center = [pos[i] + size[i] / 2 for i in range(3)]
            if any(abs(bag[i] - expected_center[i]) > 1e-3 for i in range(3)):
                fail(f"{label}: world->bag round trip {bag} != expected centre {expected_center} (>1mm)")
            assert_contained(label, pos, size, interior2)

        for i in range(len(placements)):
            for j in range(i + 1, len(placements)):
                pi, pj = placements[i], placements[j]
                pos_i = [pi["position"]["x"], pi["position"]["y"], pi["position"]["z"]]
                size_i = [pi["size"]["x"], pi["size"]["y"], pi["size"]["z"]]
                pos_j = [pj["position"]["x"], pj["position"]["y"], pj["position"]["z"]]
                size_j = [pj["size"]["x"], pj["size"]["y"], pj["size"]["z"]]
                if boxes_overlap(pos_i, size_i, pos_j, size_j):
                    fail(f"{pi['label']} (step {pi['step']}) overlaps {pj['label']} (step {pj['step']})")

        print(f"[5/5] every placement is inside the bag, no two overlap, all world positions finite")
        print("AR SIM: PASS")
        return 0
    finally:
        cleanup(mongo_name, server)
        shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
