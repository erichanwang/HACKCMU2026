#!/usr/bin/env python3
"""packbench - run the real solver over a fixture corpus and score it.

Same wiring as `server/planner.py`: `packer3d.pack_naive` / `pack_optimized` on a
`packer3d.scenario` scenario, every candidate graded by
`physics.packer3d_adapter.validate_packer3d`, best candidate picked with
`planner.rank`. Nothing here reimplements the solver.

    python3 tools/packbench/packbench.py                        # run the corpus
    python3 tools/packbench/packbench.py --json run.json         # save it
    python3 tools/packbench/packbench.py --baseline baseline.json  # per-scenario delta
    python3 tools/packbench/packbench.py --quick --baseline baseline.json --check  # pre-commit gate

Determinism: the default candidate budget is an SA *iteration* cap, so two runs with
the same seeds print byte-identical tables and a `--baseline` against the first is all
zeros. (Only the wall-clock numbers move: they are reported and saved, never compared.)
`--time` switches to the wall-clock budget the server actually uses, which is NOT
reproducible - the iteration count follows the clock - so use it to see production
behaviour, not to compare two solver revisions.

Every run stamps itself with the commit it measured, whether the tree was dirty, the
flags and the clock, so a saved run says what it is. `--check` turns the delta into an
exit code; the regression definition lives in `regressions()` and README.md.
"""
import argparse
import datetime
import json
import subprocess
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
# --quick: packing pressure + a heightmap cavity + fragility-as-binding-constraint, and
# nothing over ~21 s. Why these three: see README.md.
QUICK = ("adversarial_exact_fit.json", "camera_kit_fragile.json", "upright_bottles.json")
# A utilisation drop this large (fraction, not points) while packing the SAME items is
# the one utilisation change the gate treats as a regression. See regressions().
UTIL_DROP = 0.05


def validator_items(scenario: dict) -> list:
    """The scenario's items in the spelling `packer3d_adapter.item_metadata` actually reads.

    `packer3d.scenario._item_from_dict` accepts `keep_upright` OR the server document's
    `keepUpright`, but `item_metadata` reads only `keep_upright` - so a fixture that uses
    the camelCase spelling is PACKED upright and then GRADED as if it had never asked to
    be, and every upright violation is invisible. `server/planner.py` never hits this
    because it validates against `physics.prepack.prepare_items` output, which emits
    `keep_upright`; the harness has to do the same normalisation to match the server.
    """
    return [d | {"keep_upright": bool(d["keepUpright"])}
            if "keepUpright" in d and "keep_upright" not in d else d
            for d in scenario.get("items", [])]


def run_scenario(scenario: dict, candidates, iters, time_budget):
    """`(candidates, items_given)`; one candidate per (strategy, seed). Shape matches planner.rank."""
    container, items, _config, weights = load_scenario(scenario)
    # `weights` is the objective the SCENARIO FILE declares (5 of the 7 fixtures set
    # non-default `unpacked`/`com`); dropping it optimises a different problem than the
    # fixture describes and flips which candidate wins - see README, "Why the weights".
    vitems = validator_items(scenario)
    n_opt = sum(1 for s, _ in candidates if s == "optimized") or 1
    out = []
    for strategy, seed in candidates:
        t0 = time.perf_counter()
        if strategy == "naive":
            result = pack_naive(container, items)  # first-fit has no objective to weight
        else:
            cfg = (OptimizerConfig(time_budget_s=time_budget / n_opt, seed=seed) if time_budget
                   else OptimizerConfig(time_budget_s=0.0, max_iterations=iters, seed=seed))
            result = pack_optimized(container, items, config=cfg, weights=weights)
        result_dict = json.loads(result.to_json())  # metrics carry numpy scalars
        out.append({"strategy": strategy, "seed": seed, "solver": result_dict,
                    "seconds": time.perf_counter() - t0,
                    "validation": validate_packer3d(result_dict, items=vitems)})
    return out, len(items)


def row(candidate: dict, items_given: int) -> dict:
    m, v = candidate["solver"]["metrics"], candidate["validation"]
    return {"strategy": candidate["strategy"], "seed": candidate["seed"],
            "items_given": items_given, "items_packed": int(m["items_packed"]),
            "volume_utilization": float(m["volume_utilization"]),
            "com_lateral_offset": float(m["com_lateral_offset"]),
            "physics_valid": bool(v["valid"]), "violations": len(v["violations"]),
            "seconds": candidate["seconds"]}


def bench(fixtures: Path, candidates, iters, time_budget, only=None) -> dict:
    manifest = {}
    mpath = fixtures / "manifest.json"
    if mpath.exists():
        manifest = json.loads(mpath.read_text())
    results = []
    for path in sorted(p for p in fixtures.glob("*.json")
                       if p.name != "manifest.json" and (only is None or p.name in only)):
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


def git_state() -> dict:
    """`{"commit", "dirty"}` for the tree the solver was imported from, or nulls outside git."""
    def g(*a):
        return subprocess.run(("git", "-C", str(ROOT)) + a, capture_output=True, text=True,
                              timeout=15).stdout
    try:
        commit = g("rev-parse", "HEAD").strip() or None
        # whole-tree dirt, not just the solver: any uncommitted edit makes the numbers
        # unattributable to `commit`, which is the whole point of recording it.
        dirty = bool(g("status", "--porcelain").strip())
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}
    return {"commit": commit, "dirty": dirty}


def rel_to_root(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def stamp_run(run: dict, args, started, elapsed) -> dict:
    """Record what this run actually measured, so a saved run is self-describing."""
    run["run"] = dict(git_state(),
                      started_at=started.replace(microsecond=0).isoformat(),
                      wall_clock_s=round(elapsed, 2),
                      # repo-relative where possible: a committed baseline must not carry
                      # somebody's absolute worktree path
                      flags={"fixtures": rel_to_root(args.fixtures), "quick": bool(args.quick),
                             "strategy": args.strategy, "seed": args.seed,
                             "iters": None if args.time else args.iters,
                             "time": args.time or None})
    return run


def print_header(run: dict) -> None:
    m = run.get("run")
    if not m:
        print("packbench  (unstamped run - no commit/flags recorded)")
        print()
        return
    f = m["flags"]
    commit = (m["commit"] or "?")[:12]
    if m["dirty"]:
        commit += "-dirty"
    print(f"packbench  commit  {commit}")
    if m["dirty"]:
        print("           *** DIRTY TREE: measured against uncommitted changes, "
              "not attributable to that commit ***")
    elif m["dirty"] is None:
        print("           (not a git checkout: commit and dirty state unknown)")
    budget = f"--time {f['time']}" if f["time"] else f"--iters {f['iters']}"
    print(f"           flags   {budget} --seed {f['seed']} --strategy {f['strategy']}"
          f"{' --quick' if f['quick'] else ''}")
    print(f"           clock   {m['started_at']}  {m['wall_clock_s']:.1f}s wall")
    print()


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
    subset = run.get("run", {}).get("flags", {}).get("quick")
    for f in sorted(set(old) - {r["file"] for r in run["scenarios"]}):
        print(f"{old[f]['name'][:26]:<26} {'not run' if subset else 'GONE':>9}")
    a, ab = run["aggregate"], base["aggregate"]
    print("-" * len(DHDR))
    if a.get("scenarios") != ab.get("scenarios"):
        # aggregates over different corpora are not comparable; the per-fixture rows are.
        print(f"{'AGGREGATE':<26} {'n/a':>9}  ({a.get('scenarios')} scenarios vs "
              f"{ab.get('scenarios')} in the baseline)")
        return
    print(f"{'AGGREGATE':<26} {a['items_packed'] - ab['items_packed']:>+9d} "
          f"{(a['mean_utilization'] - ab['mean_utilization']) * 100:>+7.1f}% "
          f"{a['physics_valid'] - ab['physics_valid']:>+7d} {a['violations'] - ab['violations']:>+7d} "
          f"{(a['mean_com_offset'] - ab['mean_com_offset']) * 1000:>+10.1f}")


def regressions(run: dict, base: dict):
    """`(regressions, warnings)` for `--check`, per-scenario on the chosen ("best") plan.

    A REGRESSION (fails the gate) is, for a fixture measured by both runs:
      * fewer items packed than the baseline;
      * a new physics violation - `violations` higher than the baseline;
      * physics flipping valid -> invalid;
      * the fixture erroring out when the baseline solved it;
      * volume utilisation falling by more than UTIL_DROP while packing the SAME number
        of items - the one utilisation change that is unambiguously worse.
    NOT a regression, deliberately, because a gate that fires on these gets switched off:
      * utilisation moving by less than UTIL_DROP, or moving at all when the item count
        changed (packing one more awkward item legitimately costs utilisation);
      * centre of mass moving in any direction by any amount - it is reported, never gated;
      * wall clock;
      * a fixture the baseline does not have, or one this run did not measure (`--quick`);
      * anything in the aggregate row: over a `--quick` subset it compares different
        corpora, so the gate is per-fixture only.
    """
    old = {r["file"]: r for r in base["scenarios"]}
    new = {r["file"]: r for r in run["scenarios"]}
    regs, warns = [], []
    for f in sorted(new):
        r, o = new[f], old.get(f)
        if o is None:
            warns.append(f"{f}: not in the baseline, nothing to compare (not gated)")
            continue
        if "best" not in o:
            warns.append(f"{f}: baseline has no result for it (not gated)")
            continue
        if "best" not in r:
            regs.append(f"{f}: errored this run ({r.get('error', '?')}); "
                        f"baseline packed {o['best']['items_packed']}")
            continue
        n, b = r["best"], o["best"]
        if n["items_packed"] < b["items_packed"]:
            regs.append(f"{f}: packed {n['items_packed']} of {n['items_given']}, "
                        f"baseline packed {b['items_packed']}")
        if n["violations"] > b["violations"]:
            regs.append(f"{f}: physics violations {b['violations']} -> {n['violations']}")
        if b["physics_valid"] and not n["physics_valid"]:
            regs.append(f"{f}: physics valid -> INVALID")
        if (n["items_packed"] == b["items_packed"]
                and n["volume_utilization"] < b["volume_utilization"] - UTIL_DROP):
            regs.append(f"{f}: utilisation {b['volume_utilization'] * 100:.1f}% -> "
                        f"{n['volume_utilization'] * 100:.1f}% on the same "
                        f"{n['items_packed']} items (allowed drop {UTIL_DROP * 100:.0f} pts)")
    for f in sorted(set(old) - set(new)):
        warns.append(f"{f}: in the baseline, not measured by this run (not gated)")
    if run["config"] != base["config"]:
        warns.append(f"config differs from the baseline ({json.dumps(run['config'], sort_keys=True)} "
                     f"vs {json.dumps(base['config'], sort_keys=True)}) - the deltas compare "
                     "two different measurements")
    if run.get("run", {}).get("dirty"):
        warns.append("this run measured a DIRTY tree; a pass here does not clear the commit")
    if base.get("run", {}).get("dirty"):
        warns.append("the baseline was measured on a DIRTY tree; it is not a commit-attributable bar")
    return regs, warns


def print_check(regs, warns) -> None:
    for w in warns:
        print(f"warn: {w}")
    if regs:
        print(f"FAIL: {len(regs)} regression(s) against the baseline")
        for r in regs:
            print(f"  - {r}")
    else:
        print("PASS: no regressions against the baseline")


def selftest() -> int:
    """Assert the gate's rules on synthetic runs. No solver, no fixtures, milliseconds."""
    def mk(**best):
        b = {"items_given": 10, "items_packed": 8, "volume_utilization": 0.5,
             "com_lateral_offset": 0.01, "physics_valid": True, "violations": 0,
             "strategy": "optimized", "seed": 0, "seconds": 1.0} | best
        return {"config": {"c": 1}, "aggregate": {"scenarios": 1},
                "run": {"commit": "abc", "dirty": False, "flags": {"quick": False}},
                "scenarios": [{"file": "f.json", "name": "f", "items_given": 10, "best": b}]}

    base = mk()
    assert regressions(mk(), base)[0] == [], "an identical run must pass"
    assert regressions(mk(items_packed=9), base)[0] == [], "packing more must pass"
    assert regressions(mk(items_packed=7), base)[0], "packing fewer is a regression"
    assert regressions(mk(violations=1), base)[0], "a new violation is a regression"
    assert regressions(mk(physics_valid=False), base)[0], "valid -> invalid is a regression"
    assert regressions(mk(com_lateral_offset=9.9), base)[0] == [], "com is never gated"
    assert regressions(mk(volume_utilization=0.5 - UTIL_DROP / 2), base)[0] == [], \
        "a small utilisation drop is not gated"
    assert regressions(mk(volume_utilization=0.5 - UTIL_DROP * 2), base)[0], \
        "a utilisation collapse on the same items is a regression"
    assert regressions(mk(items_packed=9, volume_utilization=0.1), base)[0] == [], \
        "utilisation is not gated once the item count moved"
    # errored fixture vs a baseline that had a result
    err = mk()
    err["scenarios"] = [{"file": "f.json", "name": "f", "error": "boom"}]
    assert regressions(err, base)[0], "a fixture that stopped solving is a regression"
    # a fixture only one side measured is a warning, never a failure
    empty = mk()
    empty["scenarios"] = []
    regs, warns = regressions(empty, base)
    assert regs == [] and any("not measured" in w for w in warns), (regs, warns)
    regs, warns = regressions(mk(), {"config": {"c": 1}, "aggregate": {"scenarios": 0}, "scenarios": []})
    assert regs == [] and any("not in the baseline" in w for w in warns), (regs, warns)
    # config mismatch and dirty trees warn, never fail
    regs, warns = regressions(mk(), dict(base, config={"c": 2}))
    assert regs == [] and any("config differs" in w for w in warns), (regs, warns)
    dirty = mk()
    dirty["run"]["dirty"] = True
    regs, warns = regressions(dirty, base)
    assert regs == [] and any("DIRTY" in w for w in warns), (regs, warns)
    # the keep_upright normalisation the validator needs
    assert validator_items({"items": [{"id": "a", "keepUpright": True}]})[0]["keep_upright"] is True
    assert validator_items({"items": [{"id": "a", "keep_upright": False, "keepUpright": True}]}) == \
        [{"id": "a", "keep_upright": False, "keepUpright": True}], "an explicit spelling wins"
    print("selftest: ok")
    return 0


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
    ap.add_argument("--quick", action="store_true",
                    help=f"only the fast subset ({', '.join(f.split('.')[0] for f in QUICK)}); "
                         "~30 s instead of ~7 min, for running before a commit")
    ap.add_argument("--selftest", action="store_true",
                    help="assert the --check rules on synthetic runs and exit; no solver, no fixtures")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the run is WORSE than --baseline (see regressions() for "
                         "exactly what counts); requires --baseline")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    seeds = [int(s) for s in args.seed.split(",") if s.strip() != ""]
    candidates = ([("naive", None)] if args.strategy in ("naive", "both") else []) + \
                 ([("optimized", s) for s in seeds] if args.strategy in ("optimized", "both") else [])
    if not candidates:
        print("no candidates", file=sys.stderr)
        return 2
    if not args.fixtures.is_dir():
        print(f"no fixture directory: {args.fixtures}", file=sys.stderr)
        return 2

    if args.check and not args.baseline:
        print("--check needs --baseline", file=sys.stderr)
        return 2
    only = None
    if args.quick:
        only = set(QUICK)
        missing = sorted(f for f in only if not (args.fixtures / f).is_file())
        if missing:
            print(f"--quick fixtures missing from {args.fixtures}: {', '.join(missing)}", file=sys.stderr)
            return 2

    started = datetime.datetime.now().astimezone()
    t0 = time.perf_counter()
    run = bench(args.fixtures, candidates, args.iters, args.time, only)
    stamp_run(run, args, started, time.perf_counter() - t0)
    print_header(run)
    print_table(run)
    if args.times:
        print_times(run)
    if args.json:
        args.json.write_text(json.dumps(run, indent=2, sort_keys=True) + "\n")
    if not args.baseline:
        return 0
    base = json.loads(args.baseline.read_text())
    print()
    print_delta(run, base)
    if not args.check:
        return 0
    regs, warns = regressions(run, base)
    print()
    print_check(regs, warns)
    return 1 if regs else 0


if __name__ == "__main__":
    raise SystemExit(main())
