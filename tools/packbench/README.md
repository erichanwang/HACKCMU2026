# packbench

Runs the real solver (`packer3d.pack_naive` / `pack_optimized`, graded by
`physics.packer3d_adapter.validate_packer3d`, ranked by `planner.rank`) over the
fixture corpus in `fixtures/` and scores it. Nothing here reimplements the
solver; if the table is wrong, the solver is wrong.

```sh
python3 tools/packbench/packbench.py                                   # full corpus, ~2.5 min
python3 tools/packbench/packbench.py --quick                           # 4 fixtures, ~20 s
python3 tools/packbench/packbench.py --baseline tools/packbench/baseline.json
python3 tools/packbench/packbench.py --quick --baseline tools/packbench/baseline.json --check
python3 tools/packbench/packbench.py --json run.json                   # save the full run
python3 tools/packbench/packbench.py --selftest                        # the gate's own rules, no solver
```

## The harness runs what the scenario file says

The harness's job is to predict `server/planner.py`. It used to disagree with it, and
with a direct run of the same candidate at the same deterministic settings: on
`carryon_overfilled` it reported `naive 23/41 46.8%` where `optimized:0` packs 25 items,
valid, 0 violations, and should win on `planner.rank`'s `items_packed`.

**The harness was the wrong one, in exactly one place: it dropped the weights.**
`load_scenario` returns `(container, items, config, weights)` and the fourth value went
into `_weights`. Five of the seven fixtures declare non-default weights
(`adversarial_exact_fit` `unpacked: 30, com: 2`; `carryon_overfilled` `unpacked: 14,
com: 4`; `clothes_dominated` `unpacked: 12, com: 4`; `camera_kit_fragile` `unpacked: 12`;
`upright_bottles` `com: 8`), so the harness was optimising a different problem than the
fixture describes. On `carryon_overfilled` that decided the *winner*:

| | packed | util | physics | chosen |
|---|---|---|---|---|
| weights dropped | 23 | 46.8% | valid | **naive** — `optimized:0` reached 27 items but at 2 `UNSUPPORTED_OBJECT`, so `rank` put it below first-fit |
| weights passed | 25 | 48.6% | valid | **optimized:0** |

`pack_optimized(..., weights=weights)` now gets them. `pack_naive` takes none and needs
none — first-fit has no objective to weight. Ablating the change one fixture at a time,
`carryon_overfilled` is the only one whose chosen candidate moves.

The fixtures' `optimizer` blocks set nothing beyond the budget and seed that `--iters` /
`--time` / `--seed` deliberately own, so `config` is still discarded; that is a choice,
not an oversight.

### Why the harness does *not* run `physics.prepack.prepare_items`

`server/planner.py` builds its scenario items with `prepare_items(...)` and validates
against that, which raises the fair question of whether the harness should too. It should
not, and cannot:

- **A fixture already *is* prepack's output form.** `prepare_items` consumes a *scan
  document* (`dimensions: [width, height, depth]` in metres, `rigidity`,
  `compressibility`, `keepUpright`, `mass`) and emits a *packer3d scenario item*. The
  fixtures are packer3d scenario files — `dims: [l, w, h]`, `shape`, `count`, `radius`,
  `heights`/`cellSize`. Calling `prepare_items` on one raises
  `KeyError: 'dimensions'`; it would be feeding a pipeline stage its own output.
- **It would change nothing even if it ran.** prepack's whole effect on the packed
  geometry is to replace the raw scan `compressibility` with
  `height / (height - compression_allowance_m(...))`, and for a soft item under the 95%
  cap that expression *is* `k`. Translating all 39 fixture items that carry
  `compressibility > 1` into scan-document form and running them through
  `prepare_items` returns the identical `k` for every one, and no fixture has
  `compressibility > 1` on a non-soft item (which is the one case where prepack and the
  loader would disagree, prepack refusing to compress what the loader compresses).
- **The grader reads the fixture's own keys.** Since `e4590e6`, `item_metadata` squashes a
  soft item's cavity grid and height by `compressibility` under the same
  `rigidity == "soft"` gate the loader uses, and accepts `keep_upright` *or* `keepUpright`.
  Both keys are present in the fixtures in exactly those spellings, so the raw fixture
  items grade the same geometry the server's prepack output would.

Before `e4590e6` the second half of that was false: `item_metadata` read only
`keep_upright`, so the corpus's 25 camelCase `keepUpright` items — all 11 in
`upright_bottles`, whose entire purpose is upright constraints — were packed upright and
then graded as though they had never asked to be. This harness carried a normalisation
for that for one commit; `e4590e6` fixed it at the source and the normalisation is gone.

> **Do not diff against anything measured before `e4590e6`**, including every number
> quoted in `fixtures/manifest.json`'s per-fixture `baseline` blocks (`loop @ 875137d`).
> The committed `baseline.json` is stamped with the commit it measured; that stamp, not
> the manifest prose, is the machine-readable bar.

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
| a lost nest | `nested` below the baseline — fewer placements sitting in another item's scanned cavity |

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

### `nested`: the one rule that is gated whatever else moved

`nested` is how many of the chosen plan's placements carry `nested_in` — the decoder's own
record that it put an item inside an already-placed item's *scanned cavity*. It is counted
off `result.placements[].nested_in`, never derived from overlapping boxes: an overlap nobody
declared is a collision, not a nest, and re-deriving it here would relabel the one as the
other (`packer3d/decoder.py::_nested_in` says the same thing from the other side).

Unlike utilisation, it is gated **even when the item count moved**, and that asymmetry is
the whole point: `items_packed` cannot see a nesting regression *at all*. Stop the solver
nesting and the guest just goes somewhere else, or drops out while some other item takes
its place — the corpus reports the same packed count, the same validity, no violations, and
nobody learns which change did it. That is exactly the hole this rule and
`nested_foam_cutout` were added to close. It is also not noise-shaped the way utilisation
is: the count only moves when the solver's own nesting decision changes, so 1 → 0 is a
fact, not churn.

Measured, so it is not an argument from principle. Take `nested_foam_cutout` in a 4 cm wider
bag (so the guest also fits on the floor beside the case) and shift its cut-out one grid row,
which is enough to knock it off `solid_boxes`' 4x4 block boundary and pool it away — the case
then has no cavity at all. Same items, same seed, `optimized:0`:

| | packed | util | physics | violations | `nested` |
|---|---|---|---|---|---|
| cut-out block-aligned | 11/11 | 78.4% | valid | 0 | **1** |
| cut-out shifted one row | 11/11 | 78.4% | valid | 0 | **0** |

Every other number in the table is identical. The cavity path died and `nested` is the only
column that says so.

In the shipped fixture the guest fits *nowhere* else, so a lost nest also costs an item and
the item-count rule fires too — belt and braces, and the `nested` line is what names the
cause instead of leaving "−1 item" to be bisected.

Two honest limits on it:

- **Only `nested_foam_cutout` contributes a non-zero count today.** `carryon_weekend` and
  `carryon_overfilled` do nest 2 items each under first-fit (the trainers' collars), but the
  *chosen* candidate on both is the optimiser, which nests none, and the gate only ever looks
  at the chosen plan. So the corpus-wide baseline count is 1, and it is one fixture's 1.
- **A baseline recorded before this rule existed carries no count.** Those print a `warn:`
  and a `?` in the delta rather than a fabricated `+0`; re-record the baseline to gate on it.

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

## `--quick`: four fixtures, ~20 s

The full corpus is ~2.5 minutes of solver time (it was ~7 before the weights fix, which
cut `carryon_overfilled` from ~190 s to ~85 s), and `carryon_overfilled` is over half of
what is left. `--quick` runs:

| fixture | ~s | what it is the only cheap cover for |
|---|---|---|
| `adversarial_exact_fit` | 1.0 | **Packing pressure.** Five trays tile 97.7% of a Pelican 1510 with 1-2 mm slack; exactly one ordering fits. First-fit already gets 5/5, so anything below that is a real regression rather than a hard case - and it costs one second to find out. |
| `upright_bottles` | 5.1 | **A heightmap cavity**, plus upright constraints. `packing_cube_half_full` sags from a 12 cm rim to 7 cm in the middle, so nesting a bottle base into the dip is worth real volume, and `item_metadata` squashes exactly that grid by `1/k`. Eleven `allow_lay_down: false` items and a 30.5 cm wine bottle in a 31 cm bag mean a rotation bug shows up as dropped items immediately - and after `e4590e6` those eleven upright constraints are finally being graded. |
| `nested_foam_cutout` | 3.7 | **A nest that actually happens.** A hard camera case with one empty foam cut-out and a lens that fits the cut-out and nothing else, so the only way to pack it is inside the case's cavity - and `nested` in the table is 1 instead of 0. The corpus's only cover for the whole cavity path (`solid_boxes`' pooling, `irregular` classification, the decoder's fragile-below rule, `nested_in`); see `fixtures/manifest.json` for why every number in it is forced. |
| `camera_kit_fragile` | 10.3 | **Fragility as the binding resource.** ~11 L of `fragile` + `keepUpright` gear that nothing may be stacked on, so floor area runs out before volume does. Deliberately has no heightmap grids, which makes the fragile constraint the only thing being measured. It also leaves 2 of 21 items behind at 42.7% utilisation, the corpus's widest gap between what the solver manages and what the fixture notes say a person manages, so it is the fixture most likely to move. |

Packing pressure, a heightmap cavity, fragility and a real nest for ~20 s of solver time;
three consecutive `--quick` runs on this laptop took 12.5, 13.9 and 15.0 s wall before
`nested_foam_cutout` (3.7 s) joined them. Treat the per-fixture seconds as a ratio, not a
promise - they are this machine, at `--iters 40`, they moved by 2x when the weights landed,
and they move again with load: the same `--quick` took 32 s wall with several other agents
on the laptop.

The skipped fixtures are skipped for cost and redundancy: `carryon_overfilled`
(~85 s) and `clothes_dominated` (~32 s) are the expensive ones, `checked_heavy_light`
(~14 s) is a second heightmap-cavity fixture, and `carryon_weekend` (~6 s) is a gentler
version of coverage `upright_bottles` already gives. Run the full corpus before merging;
`--quick` is for the loop between commits.

`--quick` compares cleanly against the committed full-corpus `baseline.json` because the
gate is per-fixture: the skipped ones print `not run` in the delta and a `warn:` line under
`--check`.

## Determinism

The default budget is an SA *iteration* cap (`--iters`), not a clock, so two runs
with the same seeds produce the same table and `--baseline` against the first is
all zeros. Fixtures are iterated in sorted filename order and the JSON is dumped
with sorted keys, so two saved runs diff cleanly. The only part of the output that
moves between two identical runs is the header's clock line.

`--selftest` asserts every `--check` rule against synthetic run/baseline dicts in
milliseconds, with no solver and no fixtures. Run it after touching `regressions()`; it is
the thing that fails if the gate's logic breaks.
