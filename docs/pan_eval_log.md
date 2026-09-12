# PAN Evaluation / Evidence Log

PAN.md sec 24. This is a hackathon prototype evidence log, not a benchmark: no
accuracy numbers are computed or claimed anywhere below. Every row was produced
by one real invocation of `pan.demo.run_demo(backend="mock")` on
`2026-09-12` against the current `pan/` tree (`git branch --show-current` ==
`pan-integration`), reproducible with:

```
python3 -m pan demo --backend mock --frames 8 --steps 2 --out /tmp/pan_eval_run
```

**Backend note:** `PanWorldModel` resolved to `mock` -- there is no real PAN
access available (see `docs/PAN_ACCESS.md`), so every "PAN output" below is the
deterministic `MockPanBackend` rollout, never real PAN inference. Latency is
the mock's synthetic-frame-generation time, not a real inference latency.

## Scene / action pairs

| # | Candidate | Step | Object acted on | Action text (truncated) | Backend | Latency (ms) | Frames | Evaluator level | Confidence |
|---|---|---|---|---|---|---|---|---|---|
| 1 | A "shoe first" | 1 | shoe | "A traveler picks up the black running shoe... places it flat on the suitcase floor in the front-left corner, laid flat." | mock | 258 | 8 | low | 0.80 |
| 2 | A "shoe first" | 2 | camera | "A traveler picks up the mirrorless camera... places it on top of the toiletry bag in the front-right corner, laid flat." | mock | 224 | 8 | high | 0.80 |
| 3 | B "camera first" | 1 | camera | "A traveler picks up the mirrorless camera... places it on top of the toiletry bag in the front-right corner, laid flat." | mock | 256 | 8 | high | 0.80 |
| 4 | B "camera first" | 2 | shoe | "A traveler picks up the black running shoe... places it flat on the suitcase floor in the front-left corner, laid flat." | mock | 221 | 8 | high | 0.80 |
| 5 | C "camera on laptop" | 1 | camera | "A traveler picks up the mirrorless camera... places it on top of the 13-inch laptop in the rear-left corner, laid flat." | mock | n/a (gated) | 0 | n/a | n/a |

Row 5 never reached PAN at all: `validate_layout` rejects it first with
`FRAGILE_OBJECT_OVERLOADED` on the laptop (`cannot_support_weight=True`), so
`RolloutManager`'s physics gate marks it `unavailable` before any PAN call is
made (PAN.md sec 17) -- included deliberately, to show the gate itself being
exercised, not just the happy path.

## Human judgment (fill in after a REAL PAN run -- not the mock)

The four columns below are intentionally left unchecked. Nothing here has been
judged against real PAN output because no real PAN output exists yet (see
`docs/PAN_ACCESS.md`). Do not check these from the mock rollouts above -- a mock
rollout is a synthetic, deterministic image transform, not a prediction, so
"plausible?" / "preserves identity?" / etc. have no meaningful answer for it.

| # | Candidate / step | Plausible? | Identities preserved? | Action followed? | Major physical inconsistency? |
|---|---|---|---|---|---|
| 1 | A / shoe | [ ] | [ ] | [ ] | [ ] |
| 2 | A / camera | [ ] | [ ] | [ ] | [ ] |
| 3 | B / camera | [ ] | [ ] | [ ] | [ ] |
| 4 | B / shoe | [ ] | [ ] | [ ] | [ ] |
| 5 | C / camera (gated, no PAN call) | n/a -- never reached PAN | n/a | n/a | n/a |

**When real PAN access exists:** re-run `python3 -m pan demo --backend pan`,
replace rows 1-5 above with the real backend's latency/frame count/evaluator
output, and have a human fill in every checkbox for rows 1-4 by watching the
actual `rollout.gif` next to `expected.png` for that step. Do not fabricate
these checkmarks or any derived accuracy percentage (PAN.md sec 24).
