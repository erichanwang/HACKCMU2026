"""CLI for the PAN demo pipeline: `python3 -m pan demo` / `python3 -m pan status`."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from pan.demo import run_demo
from pan.world_model import RealPanBackend, get_world_model

# Our own convention (docs/PAN_ACCESS.md (d)) -- never IFM's. Names only, never values.
_PAN_ENV_VARS = ("PAN_API_KEY", "PAN_BASE_URL", "PAN_MODEL", "PAN_TIMEOUT_S", "PAN_ENDPOINT_PATH")


def _status() -> int:
    model = get_world_model("auto")
    real_available = RealPanBackend().available()
    set_vars = [v for v in _PAN_ENV_VARS if os.environ.get(v)]
    unset_vars = [v for v in _PAN_ENV_VARS if v not in set_vars]
    print(f"resolved backend: {model.name}")
    print(f"real PAN configured: {real_available}")
    print(f"env vars set (names only): {', '.join(set_vars) or 'none'}")
    print(f"env vars unset: {', '.join(unset_vars) or 'none'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pan")
    sub = parser.add_subparsers(dest="command", required=True)

    demo_p = sub.add_parser("demo", help="run the end-to-end mock/PAN packing demo")
    demo_p.add_argument("--out", default="out/pan_demo")
    demo_p.add_argument("--backend", default="auto", choices=["auto", "mock", "pan"])
    demo_p.add_argument("--steps", type=int, default=2)
    demo_p.add_argument("--frames", type=int, default=8)
    demo_p.add_argument("--viewpoint", default="overhead_45")

    sub.add_parser("status", help="show which world-model backend is resolved and why")

    args = parser.parse_args(argv)

    if args.command == "demo":
        run_demo(args.out, backend=args.backend, steps=args.steps, num_frames=args.frames, viewpoint=args.viewpoint)
        print((Path(args.out) / "summary.txt").read_text())
        return 0
    if args.command == "status":
        return _status()
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
