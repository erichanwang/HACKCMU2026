# packbench

Runs the real solver (`packer3d.pack_naive` / `pack_optimized`, graded by
`physics.packer3d_adapter.validate_packer3d`, ranked by `planner.rank`) over the
seven-fixture corpus in `fixtures/` and scores it. Nothing here reimplements the
solver; if the table is wrong, the solver is wrong.

```sh
python3 tools/packbench/packbench.py                                   # full corpus, ~7 min
python3 tools/packbench/packbench.py --quick                           # 3 fixtures, ~30 s
python3 tools/packbench/packbench.py --baseline tools/packbench/baseline.json
python3 tools/packbench/packbench.py --quick --baseline tools/packbench/baseline.json --check
python3 tools/packbench/packbench.py --json run.json                   # save the full run
python3 tools/packbench/packbench.py --selftest                        # the gate's own rules, no solver
```

## The harness runs what the scenario file says

`load_scenario` returns `(container, items, config, weights)` and the harness used to
throw `weights` away. Five of the seven fixtures declare non-default weights
(`adversarial_exact_fit` `unpacked: 30, com: 2`, `carryon_overfilled` `unpacked: 14,
com: 4`, `clothes_dominated` `unpacked: 12, com: 4`, `camera_kit_fragile` `unpacked:
12`, `upright_bottles` `com: 8`), so the harness was optimising a different objective
than the fixture describes. On `carryon_overfilled` that decided the *winner*:

| | packed | util | physics | chosen |
|---|---|---|---|---|
| weights dropped | 23 | 46.8% | valid | **naive** — `optimized:0` reached 27 items but at 2 `UNSUPPORTED_OBJECT`, so `rank` put it below first-fit |
| weights passed | 25 | 48.6% | valid | **optimized:0** |

`pack_optimized(..., weights=weights)` now gets them. `pack_naive` does not take
weights and does not need them — first-fit has no objective to weight.

The harness also normalises one spelling before grading. `packer3d.scenario` accepts
`keep_upright` *or* the server document's `keepUpright`, but
`physics.packer3d_adapter.item_metadata` reads only `keep_upright` — so a fixture using
the camelCase spelling was packed upright and then graded as though it had never asked
to be, and `upright_bottles`, whose entire point is eleven upright items, had its upright
constraint checked on *zero* of them. `server/planner.py` never hits this because it
validates against `physics.prepack.prepare_items` output, which emits `keep_upright`;
`validator_items()` does the same normalisation so the harness matches the server. It
changes no number in today's corpus — the solver was already respecting the constraint —
but the check is now actually running.

The fixtures' `optimizer` blocks set nothing beyond the budget and seed that `--iters` /
`--time` / `--seed` deliberately own, so `config` is still discarded; that is a
deliberate choice, not an oversight.

> The committed `baseline.json` was re-recorded after both of these. Do not diff against
> anything measured before them — `carryon_overfilled` moves from `naive 23/41 46.8%` to
> `optimized:0 25/41 48.6%`, which is the fix, not a regression.

## `--check`: what counts as a regression

`--check` requires `--baseline` and exits **1** if the run is worse than that
baseline, **0** otherwise. It compares only the *chosen* plan (`best`) per
fixture, never the aggregate row.

A **regression**, for a fixture that both runs measured:

| | |
|---|---|
| fewer items packed | `items_packed` below the baseline |
| a new physics violation | `violations` above the baseline |
| a validity flip | baseline `physics_valid: true`, run `false` |
| a fixture that stopped solving | the run errored where the baseline had a result |
| utilisation collapse | `volume_utilization` more than `UTIL_DROP` (5 points) below the baseline **while packing the same number of items** |

Explicitly **not** a regression, because a gate that fires on these gets
switched off inside a day:

- **Utilisation moving on its own.** Within 5 points it is noise-shaped churn
  from a solver that is changing hourly. Any size of change is ignored when the
  item count also changed: packing one more awkward item legitimately costs
  utilisation, and packing one fewer is already caught by the item-count rule.
- **Centre of mass, in any direction, by any amount.** It is reported in the
  table and the delta because it is diagnostic, but there is no agreed target
  for it, so gating on it would be gating on an opinion.
- **Wall clock.** This machine is not a benchmark rig.
- **Fixtures only one side has.** A fixture the baseline lacks, or one this run
  skipped (`--quick`), prints a `warn:` line and is not gated. A fixture that is
  in the run and *fails* is a regression; a fixture that was never run cannot be.
- **The aggregate row.** Over a `--quick` subset it sums a different corpus than
  the baseline's, so `--baseline` prints `n/a` for it rather than a fake delta.

Everything the gate decides on is printed as a reason, not just folded into the
exit code. `warn:` lines never fail the run; they say why a comparison might be
meaningless — a config mismatch (different `--iters`/`--seed`/`--strategy`, so
the two runs measured different things), or either side having been measured on
a dirty tree.

## A run says what it is

Both the header and the saved JSON (`run` key) record:

- the **commit** the run measured, and whether the working tree was **dirty** —
  a dirty run is marked with a `*** DIRTY TREE ***` banner in the header and a
  `-dirty` suffix on the commit, because a number measured against uncommitted
  edits is not attributable to that commit;
- the **flags** (`--iters` or `--time`, `--seed`, `--strategy`, `--quick`, the
  fixture directory);
- the **wall clock**: when the run started and how long it took.

Dirty means *anything* uncommitted anywhere in the tree, not just under
`physics/` or `packer3d/` — the point of the stamp is attributability, and a
half-applied patch two directories over still makes the run unattributable.

The header's clock lines are the only part of the output that changes between two
identical runs; the table and the delta are byte-identical for the same
`--iters`/`--seed` (`--time` is a wall-clock budget and therefore *not*
reproducible — use it to see production behaviour, never to compare revisions).

## `--quick`: three fixtures, ~30 s

The full corpus is ~7 minutes of solver time and `carryon_overfilled` alone is
over 3, which is too slow to run before a commit. `--quick` runs:

| fixture | ~s | what it is the only cheap cover for |
|---|---|---|
| `adversarial_exact_fit` | 5 | **Packing pressure.** Five trays tile 97.7% of a Pelican 1510 with 1–2 mm slack; exactly one ordering fits. First-fit already gets 5/5, so anything below that is a real regression rather than a hard case — and it costs 5 seconds to find out. |
| `upright_bottles` | 10 | **A heightmap cavity**, plus upright constraints. `packing_cube_half_full` sags from a 12 cm rim to 7 cm in the middle, so nesting a bottle base into the dip is worth real volume. Eleven `allow_lay_down: false` items and a 30.5 cm wine bottle in a 31 cm bag mean a rotation bug shows up as dropped items immediately. |
| `camera_kit_fragile` | 21 | **Fragility as the binding resource.** ~11 L of `fragile` + `keepUpright` gear that nothing may be stacked on, so floor area runs out before volume does. Deliberately has no heightmap grids, which makes the fragile constraint the only thing being measured. It also leaves 2 of 21 items behind at 42.7% utilisation, the corpus's widest gap between what the solver manages and what the fixture notes say a person manages, so it is the fixture most likely to move. |

That is packing pressure, a heightmap cavity and fragility in well under a
minute - four consecutive runs on this laptop took 25.4, 31.2, 31.9 and 43.2 s,
so treat the per-fixture seconds above as a ratio, not a promise. The four skipped fixtures are skipped for cost: `carryon_overfilled`
(~190 s), `clothes_dominated` (~120 s) and `checked_heavy_light` (~36 s) are the
expensive ones, and `carryon_weekend` (~20 s) is a gentler version of coverage
`upright_bottles` already gives. Run the full corpus before merging; `--quick` is
for the loop between commits.

`--quick` compares cleanly against the committed full-corpus `baseline.json`
because the gate is per-fixture: the other four print `not run` in the delta and
a `warn:` line under `--check`.

## Determinism

The default budget is an SA *iteration* cap (`--iters`), not a clock, so two runs
with the same seeds produce the same table and `--baseline` against the first is
all zeros. Fixtures are iterated in sorted filename order and the JSON is dumped
with sorted keys, so two saved runs diff cleanly. The only part of the output that
moves between two identical runs is the header's clock line.

`--selftest` asserts the `--check` rules (and the `keep_upright` normalisation) against
synthetic run/baseline dicts in milliseconds, with no solver and no fixtures. Run it
after touching `regressions()`; it is the thing that fails if the gate's logic breaks.
