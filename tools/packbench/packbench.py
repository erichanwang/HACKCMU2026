#!/usr/bin/env python3
"""packbench - run the real solver over a fixture corpus and score it.

Same wiring as `server/planner.py`: `packer3d.pack_naive` / `pack_optimized` on a
`packer3d.scenario` scenario, every candidate graded by
`physics.packer3d_adapter.validate_packer3d`, best candidate picked with
`planner.rank`. Nothing here reimplements the solver.

    python3 tools/packbench/packbench.py                        # run the corpus
    python3 tools/packbench/packbench.py --json run.json         # save it
    python3 tools/packbench/packbench.py --baseline baseline.json  # per-scenario delta

Determinism: the default candidate budget is an SA *iteration* cap, so two runs with
the same seeds print byte-identical tables and a `--baseline` against the first is all
zeros. (Only the wall-clock numbers move: they are reported and saved, never compared.)
`--time` switches to the wall-clock budget the server actually uses, which is NOT
reproducible - the iteration count follows the clock - so use it to see production
behaviour, not to compare two solver revisions.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT, ROOT / "server", ROOT / "packer3d"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from packer3d import OptimizerConfig, pack_naive, pack_optimized  # noqa: E402
from packer3d.scenario import load_scenario  # noqa: E402
from physics.packer3d_adapter import validate_packer3d  # noqa: E402
from planner import rank  # noqa: E402  - the production ranking, not a copy

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DEFAULT_ITERS = 40  # ~8 min for the 7-fixture corpus; the solver decodes at ~0.5-2 s per iteration


def run_scenario(scenario: dict, candidates, iters, time_budget):
    """`(candidates, items_given)`; one candidate per (strategy, seed). Shape matches planner.rank."""
    container, items, _config, _weights = load_scenario(scenario)
    n_opt = sum(1 for s, _ in candidates if s == "optimized") or 1
    out = []
    for strategy, seed in candidates:
        t0 = time.perf_counter()
        if strategy == "naive":
            result = pack_naive(container, items)
        else:
            cfg = (OptimizerConfig(time_budget_s=time_budget / n_opt, seed=seed) if time_budget
                   else OptimizerConfig(time_budget_s=0.0, max_iterations=iters, seed=seed))
            result = pack_optimized(container, items, config=cfg)
        result_dict = json.loads(result.to_json())  # metrics carry numpy scalars
        out.append({"strategy": strategy, "seed": seed, "solver": result_dict,
                    "seconds": time.perf_counter() - t0,
                    "validation": validate_packer3d(result_dict, items=scenario.get("items", []))})
    return out, len(items)


def row(candidate: dict, items_given: int) -> dict:
    m, v = candidate["solver"]["metrics"], candidate["validation"]
    return {"strategy": candidate["strategy"], "seed": candidate["seed"],
            "items_given": items_given, "items_packed": int(m["items_packed"]),
            "volume_utilization": float(m["volume_utilization"]),
            "com_lateral_offset": float(m["com_lateral_offset"]),
            "physics_valid": bool(v["valid"]), "violations": len(v["violations"]),
            "seconds": candidate["seconds"]}


def bench(fixtures: Path, candidates, iters, time_budget) -> dict:
    manifest = {}
    mpath = fixtures / "manifest.json"
    if mpath.exists():
        manifest = json.loads(mpath.read_text())
    results = []
    for path in sorted(p for p in fixtures.glob("*.json") if p.name != "manifest.json"):
        meta = manifest.get(path.name, {})
        entry = {"file": path.name, "name": meta.get("name", path.stem), "notes": meta.get("notes", "")}
        try:
            scenario = json.loads(path.read_text())
            cands, items_given = run_scenario(scenario, candidates, iters, time_budget)
        except Exception as exc:  # a corpus fixture is external input; one bad file must not kill the run
            entry["error"] = f"{type(exc).__name__}: {exc}"
            results.append(entry)
            continue
        cands.sort(key=rank, reverse=True)
        entry["items_given"] = items_given
        entry["best"] = row(cands[0], items_given)
        entry["candidates"] = [row(c, items_given) for c in cands]
        results.append(entry)
    return {"config": {"candidates": [[s, i] for s, i in candidates],
                       "iters": None if time_budget else iters, "time_budget_s": time_budget},
            "scenarios": results, "aggregate": aggregate(results)}


def aggregate(results) -> dict:
    ok = [r for r in results if "best" in r]
    if not ok:
        return {"scenarios": 0}
    given = sum(r["best"]["items_given"] for r in ok)
    packed = sum(r["best"]["items_packed"] for r in ok)
    return {"scenarios": len(ok), "errors": len(results) - len(ok),
            "items_packed": packed, "items_given": given,
            "pack_rate": packed / given if given else 0.0,
            "mean_utilization": sum(r["best"]["volume_utilization"] for r in ok) / len(ok),
            "physics_valid": sum(1 for r in ok if r["best"]["physics_valid"]),
            "violations": sum(r["best"]["violations"] for r in ok),
            "mean_com_offset": sum(r["best"]["com_lateral_offset"] for r in ok) / len(ok),
            "seconds": sum(c["seconds"] for r in ok for c in r["candidates"])}


HDR = f"{'scenario':<26} {'packed':>9} {'util':>7} {'phys':>5} {'viol':>5} {'com(mm)':>8}  chosen"


def print_table(run: dict) -> None:
    print(HDR)
    print("-" * len(HDR))
    for r in run["scenarios"]:
        if "best" in r:
            b = r["best"]
            chosen = b["strategy"] if b["seed"] is None else f"{b['strategy']}:{b['seed']}"
            print(f"{r['name'][:26]:<26} {b['items_packed']:>4}/{b['items_given']:<4} "
                  f"{b['volume_utilization'] * 100:>6.1f}% {'yes' if b['physics_valid'] else 'NO':>5} "
                  f"{b['violations']:>5} {b['com_lateral_offset'] * 1000:>8.1f}  {chosen}")
        else:
            print(f"{r['name'][:26]:<26} {'ERROR':>9}  {r['error'][:60]}")
    a = run["aggregate"]
    print("-" * len(HDR))
    if not a.get("scenarios"):
        print("no scenarios")
        return
    print(f"{'AGGREGATE (' + str(a['scenarios']) + ' scenarios)':<26} {a['items_packed']:>4}/{a['items_given']:<4} "
          f"{a['mean_utilization'] * 100:>6.1f}% {str(a['physics_valid']) + '/' + str(a['scenarios']):>5} "
          f"{a['violations']:>5} {a['mean_com_offset'] * 1000:>8.1f}")


def print_times(run: dict) -> None:
    print("\nwall clock per candidate (s)")
    for r in run["scenarios"]:
        for c in r.get("candidates", []):
            tag = c["strategy"] if c["seed"] is None else f"{c['strategy']}:{c['seed']}"
            print(f"  {r['name'][:26]:<26} {tag:<12} {c['seconds']:7.2f}")
    print(f"  {'TOTAL':<26} {'':<12} {run['aggregate'].get('seconds', 0.0):7.2f}")


DHDR = f"{'scenario':<26} {'d packed':>9} {'d util':>8} {'d phys':>7} {'d viol':>7} {'d com(mm)':>10}"


def print_delta(run: dict, base: dict) -> None:
    old = {r["file"]: r for r in base["scenarios"]}
    print(DHDR)
    print("-" * len(DHDR))
    for r in run["scenarios"]:
        o = old.get(r["file"])
        if o is None:
            print(f"{r['name'][:26]:<26} {'NEW':>9}")
            continue
        if "best" not in r or "best" not in o:
            print(f"{r['name'][:26]:<26} {'ERROR':>9}")
            continue
        n, b = r["best"], o["best"]
        phys = int(n["physics_valid"]) - int(b["physics_valid"])
        print(f"{r['name'][:26]:<26} {n['items_packed'] - b['items_packed']:>+9d} "
              f"{(n['volume_utilization'] - b['volume_utilization']) * 100:>+7.1f}% {phys:>+7d} "
              f"{n['violations'] - b['violations']:>+7d} "
              f"{(n['com_lateral_offset'] - b['com_lateral_offset']) * 1000:>+10.1f}")
    for f in sorted(set(old) - {r["file"] for r in run["scenarios"]}):
        print(f"{old[f]['name'][:26]:<26} {'GONE':>9}")
    a, ab = run["aggregate"], base["aggregate"]
    print("-" * len(DHDR))
    print(f"{'AGGREGATE':<26} {a['items_packed'] - ab['items_packed']:>+9d} "
          f"{(a['mean_utilization'] - ab['mean_utilization']) * 100:>+7.1f}% "
          f"{a['physics_valid'] - ab['physics_valid']:>+7d} {a['violations'] - ab['violations']:>+7d} "
          f"{(a['mean_com_offset'] - ab['mean_com_offset']) * 1000:>+10.1f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixtures", type=Path, default=FIXTURES)
    ap.add_argument("--strategy", choices=("naive", "optimized", "both"), default="both")
    ap.add_argument("--seed", default="0", help="comma-separated seeds for the optimised candidates "
                    "(server/planner.py runs 0,1,2 - that is 3x the wall clock here)")
    ap.add_argument("--iters", type=int, default=DEFAULT_ITERS, help="SA iteration cap per candidate (deterministic)")
    ap.add_argument("--time", type=float, default=0.0, metavar="S",
                    help="wall-clock budget shared by the optimised candidates, like server/planner.py "
                         "(overrides --iters; NOT reproducible)")
    ap.add_argument("--json", type=Path, help="write the full run here")
    ap.add_argument("--baseline", type=Path, help="print a per-scenario delta against a saved run")
    ap.add_argument("--times", action="store_true", help="also print wall clock per candidate")
    args = ap.parse_args(argv)

    seeds = [int(s) for s in args.seed.split(",") if s.strip() != ""]
    candidates = ([("naive", None)] if args.strategy in ("naive", "both") else []) + \
                 ([("optimized", s) for s in seeds] if args.strategy in ("optimized", "both") else [])
    if not candidates:
        print("no candidates", file=sys.stderr)
        return 2
    if not args.fixtures.is_dir():
        print(f"no fixture directory: {args.fixtures}", file=sys.stderr)
        return 2

    run = bench(args.fixtures, candidates, args.iters, args.time)
    print_table(run)
    if args.times:
        print_times(run)
    if args.json:
        args.json.write_text(json.dumps(run, indent=2, sort_keys=True) + "\n")
    if args.baseline:
        print()
        print_delta(run, json.loads(args.baseline.read_text()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
