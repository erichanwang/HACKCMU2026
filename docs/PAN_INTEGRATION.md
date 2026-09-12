# PAN Integration

## Thesis

We convert a traveler's real suitcase and objects into a scene graph, search for
packing candidates, and validate each one with a deterministic physics engine.
Before committing to an action, we hand the validated candidate to IFM's PAN
world model to imagine how it would actually unfold. The boundary is exact and
never blurred:

> Our deterministic geometry verifies feasibility, while PAN provides a learned
> simulation of how the packing action may unfold in the real world.

Physics decides pass/fail. PAN never overrides that; it adds a probabilistic,
visual "what might happen" signal on top of layouts physics already accepted.

## Architecture

```
solver (candidate next actions)
        v
physics gate            physics.validator.validate_layout via pan.rollouts.packing_subset
        v
observation adapter     pan.observation.observation_from_scene / observation_from_image
        v
action language         pan.actions.describe_action
        v
world model (mock|real) pan.world_model.{MockPanBackend,RealPanBackend,CachingWorldModel}
        v
evaluator                pan.evaluate.evaluate_rollout  (visual risk proxies, never a gate)
        v
candidate reports        pan.rollouts.RolloutBatch.{reports,candidate_reports}
        v
renderer / AR            candidates.json (below) -- consumer needs no PAN-specific knowledge
```

Only physics-valid steps ever reach the world model (`RolloutManager._physics_gate`,
on by default) -- PAN inference is never spent on layouts already known to be
impossible (PAN.md sec 17).

## Module table

| File | Responsibility | Key functions |
|---|---|---|
| `pan/types.py` | Provider-neutral contract | `Observation`, `PackingAction`, `SimulationRequest/Result`, `CandidateReport`, `WorldModel` protocol |
| `pan/observation.py` | Scene -> RGB observation (Mode A: real iPhone frame, Mode B: rendered digital twin) | `observation_from_scene`, `observation_from_image`, `save_observation` |
| `pan/actions.py` | Grounded, deterministic action language | `describe_action`, `describe_sequence`, `fill_action_text` |
| `pan/world_model.py` | Backends: mock (offline, deterministic), real (HTTP seam), caching wrapper | `MockPanBackend`, `RealPanBackend`, `CachingWorldModel`, `get_world_model` |
| `pan/evaluate.py` | Rollout frames -> structured, honest risk signals | `evaluate_rollout`, `compose_side_by_side`, `save_png` |
| `pan/rollouts.py` | Async counterfactual rollout manager | `RolloutManager.simulate_candidate_actions`, `RolloutBatch.reports`/`candidate_reports`, `rank_candidates`/`rank_candidate_rollups`, `packing_subset` |
| `pan/demo.py` | Deterministic end-to-end demo pipeline (this deliverable) | `build_demo_state`, `run_demo`, `persist_result` |
| `pan/__main__.py` | CLI | `python3 -m pan demo`, `python3 -m pan status` |
| `physics/validator.py` | Deterministic hard-constraint gate (owned by the physics team) | `validate_layout` |

## Data contract for the renderer teammate

`out_dir/candidates.json` (written by `run_demo`) has this shape:

```jsonc
{
  "backend": "cache(mock)",       // world_model.name -- string, not an API detail
  "pan_available": true,          // world_model.available()
  "returned_after_ms": 34.9,      // the solver path never waited on PAN for this
  "candidates": [ /* one CandidateReport.to_dict() per candidate, rolled up over steps */ ],
  "steps": [ /* one per (candidate, step), same shape, finer-grained */ ]
}
```

Each report (candidate or step) is plain JSON, per PAN.md sec 20/19:

- `physics_status`: `"valid" | "invalid" | "malformed"` -- the hard gate, always authoritative.
- `simulation_status`: `"pending" | "complete" | "failed" | "unavailable"`.
- `execution_risk`: `"low" | "medium" | "high" | null` -- a learned hint, never a gate.
- `action_text`: the grounded natural-language instruction sent to PAN.
- `pan_preview_video` / `pan_final_frame`: relative paths (GIF / PNG) under `out_dir`, or `null`.
- `risk_metadata`: the full `RiskSignals` evidence/notes, so a human can audit the number, not just trust it.
- `score_components`: `geometry_score`, `stability_score`, `pan_risk`, `execution_risk_weight`, `total` --
  **always logged separately** (PAN.md sec 19: never one opaque "AI score").

The renderer needs none of PAN's wire format -- it only ever reads this JSON plus
the PNG/GIF paths inside it.

## Access status

**No real PAN API/SDK exists anywhere yet** -- see `docs/PAN_ACCESS.md` for the full
recon (31 links on ifm.ai, PyPI, GitHub, HuggingFace, HackCMU/Devpost -- all checked,
none found). `RealPanBackend` is real plumbing (timeouts, retries, redacted
errors) sitting behind two explicitly marked seams in `pan/world_model.py`:
`_build_payload` (request shape) and `_parse_response` (response shape), each
tagged `# TODO(verify against docs/PAN_ACCESS.md)`. When real access appears,
only those two functions need to change. Configuration is `.env.example`'s
`PAN_API_KEY` / `PAN_BASE_URL` / `PAN_MODEL` / `PAN_TIMEOUT_S` / `PAN_ENDPOINT_PATH`
-- our own convention, not IFM's. **The mock backend is the entire demo path
today; nothing in this repo has ever called real PAN inference.**

## Failure tolerance

`RolloutManager.simulate_candidate_actions` returns immediately with `pending`
records; only genuinely PAN-bound work runs on background worker threads
(PAN.md sec 12). A candidate whose physics gate fails, or whose world model is
`unavailable()`, resolves synchronously to `unavailable` -- zero PAN calls, zero
wait. `CachingWorldModel` persists every completed rollout to disk keyed on
(observation, action, history) so identical inference is never repeated.
Nothing here can make the packing solver or AR path unusable: `run_demo`
completes and writes `candidates.json` even if PAN is fully unavailable.

## Running the demo

```
python3 -m pan demo --backend mock --out out/pan_demo
python3 -m pan status   # which backend resolves, and which env vars (names only) are set
```

Outputs in `out_dir`: `state_observation.png` (current scene), `candidates.json`,
`summary.txt` (judge-readable), `comparison.png` (first/last/expected grid across
candidates), and per-step `candidates/<id>/step_<n>/{frame_*.png,rollout.gif,expected.png}`.

3-minute demo script (PAN.md sec 13, adapted to these outputs): open
`state_observation.png` -- "the shoe and camera are still on the table, everything
else is packed." Run `python3 -m pan demo`; print `summary.txt` -- "physics says A
and B both fit; C is rejected outright because it puts the camera's full weight on
a fragile laptop." Open `comparison.png` -- "first / last / expected frame for each
candidate's first move." Land on: "our solver tells us what fits, physics tells us
what's feasible, and PAN lets us imagine execution before the traveler commits."

## Honest limitations

- The mock backend is a deterministic, offline stand-in -- it has never talked to
  real PAN inference, and every rollout in this repo today is synthetic.
- `pan.evaluate`'s risk signals are visual proxies (2D pixel/segmentation heuristics
  from one learned rollout) with an explicit `confidence` and per-signal caveats in
  `notes`/`evidence` -- never claim model accuracy from them (PAN.md sec 24).
- Single fixed viewpoint (`overhead_45` by default); no multi-camera fusion.
- No physics is recovered from rollout frames -- feasibility is decided exclusively
  by `physics.validator.validate_layout`, never by anything PAN-derived.
