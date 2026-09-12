"""Command line: python -m packer3d.cli scenario.json [--time 6] [--compare] [--out result.json] [--gap]"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace

from .bounds import gap_report
from .scenario import load_scenario
from .search import pack_naive, pack_optimized
from .verify import verify


def _row(name, r, ok):
    m = r.metrics
    n_all = m["items_packed"] + m["items_unpacked"]
    return (f"{name:<10} {m['items_packed']:>3}/{n_all:<3} packed  util {m['volume_utilization']:6.1%}  "
            f"CoM lateral {m['com_lateral_offset'] * 100:6.2f} cm  height {m['max_height_fraction']:5.0%} of H  "
            f"mass {m['total_mass']:8.1f}  objective {m['objective']:.4f}  verify: {'OK' if ok else 'FAIL'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="packer3d - pack a scenario JSON")
    ap.add_argument("scenario")
    ap.add_argument("--time", type=float, default=None, help="time budget in seconds (overrides scenario)")
    ap.add_argument("--iterations", type=int, default=None, help="SA iteration cap")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--compare", action="store_true", help="also run the naive first-fit packer")
    ap.add_argument("--gap", action="store_true", help="print distance to provable bounds")
    ap.add_argument("--out", default=None, help="write result JSON here")
    args = ap.parse_args(argv)

    container, items, config, weights = load_scenario(args.scenario)
    if args.time is not None:
        config = replace(config, time_budget_s=args.time)
    if args.iterations is not None:
        config = replace(config, max_iterations=args.iterations)
    if args.seed is not None:
        config = replace(config, seed=args.seed)

    out = {}
    if args.compare:
        naive = pack_naive(container, items)
        errs = verify(naive, items)
        print(_row("naive", naive, not errs))
        for e in errs:
            print("   !", e)
        out["naive"] = naive.to_dict()

    opt = pack_optimized(container, items, config, weights=weights)
    errs = verify(opt, items)
    print(_row("optimized", opt, not errs))
    for e in errs:
        print("   !", e)
    s = opt.stats
    print(f"  search: {s['multistart_runs']} greedy starts, {s['sa_iterations']} SA iterations "
          f"({s['sa_improvements']} improvements), {s['balance_swaps']} balancing swaps, {s['time_s']:.1f} s")
    if opt.unpacked:
        print("  unpacked:")
        for u in opt.unpacked:
            print(f"    - {u['id']}: {u['reason']}")
    if args.gap:
        gap_report(opt, items)
    out["optimized"] = opt.to_dict()

    if args.out:
        payload = out if args.compare else out["optimized"]
        with open(args.out, "w") as fh:
            json.dump(payload, fh, indent=2, default=lambda o: o.tolist() if hasattr(o, "tolist") else None)
        print(f"  wrote {args.out}")
    return 0 if not errs else 1


if __name__ == "__main__":
    sys.exit(main())
