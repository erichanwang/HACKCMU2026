# HackCMU 2026 — PAN World Model Integration Orchestrator

## Implementation Status (2026-09-12)

- **Real IFM access: confirmed, but it is not the visual PAN world model.**
  An API key landed in `.env`. Verified against the actual public docs
  (https://docs.ifm.ai/, via a real browser session — the docs site sits
  behind a Cloudflare bot challenge that blocks plain HTTP fetches): IFM's
  hosted API is `https://api.ifm.ai/v1/chat/completions`, Bearer auth, and
  the **only** model with hosted API access is `IFM/K2-Horizon-375B-A23B` —
  a text reasoning model, no vision input, no image/video output. There is
  no PAN model, world-model, or generate/simulate endpoint anywhere on that
  platform. The visual world model from the PAN paper / ifm.ai/pan/ is not
  part of this API surface — confirmed, not assumed; no endpoint was
  invented per section 10.
- **Done (P0, section 23):** `physics/pan.py` — provider-neutral
  `WorldModel` interface, `MockPanBackend` (offline/deterministic), and
  `RealPanBackend` (live, uses the API key above). Since there's no visual
  rollout available, `RealPanBackend` asks K2 Horizon to reason in text
  about a placement action and returns structured risk signals via JSON
  Schema mode (section 5, Level 3: `accessibility_risk`, `visible_shift`,
  `possible_topple`, `occlusion_risk`, `confidence`, `rationale`) instead of
  a predicted frame/video — labeled as such in its own docstring and in
  `SimulationResult.metadata["note"]`, so nothing downstream can mistake it
  for a real visual PAN rollout. Also: the scene→observation adapter
  (`build_observation`), the grounded action-text generator
  (`describe_action`), and the physics-gated rollout manager
  (`simulate_candidate_actions` — only physics-valid candidates reach the
  world model, per section 17, real or mock). Tests in `tests/test_pan.py`
  (network calls mocked, no live API hit in the suite); end-to-end demo in
  `examples/pan_demo.py` — verified against the live API
  (`python3 examples/pan_demo.py`), falls back to the mock if `IFM_API_KEY`
  isn't set.
- **Not done (P1/P2):** async/caching beyond the mock's determinism,
  multi-step continuation, world-model-aware candidate ranking, anything
  visual (no image/video generation exists to build on). There's also no
  packing solver yet (see `README.md`), so `simulate_candidate_actions`
  takes hand-authored candidate actions rather than solver output.

---

You are the lead engineering orchestrator integrating **IFM PAN** into our HackCMU 2026 project.

## Agent Configuration

Use:

- **Orchestrator:** Fable 5.1, XHigh reasoning
- **Subagents:** Sonnet 5, Medium reasoning
- Spawn **at least 5 Sonnet 5 Medium subagents concurrently**.
- Prefer 6–7 agents when there is useful parallel work.
- You are responsible for architecture, integration, review, testing, and final commits.
- Do not stop at a plan. Ship working integration.

This is a 24-hour hackathon. Optimize for:

1. a working end-to-end demo,
2. clear technical depth,
3. real use of PAN rather than a decorative API call,
4. direct relevance to both the **Traveling** track and the **IFM/PAN** sponsor prize,
5. a story judges can understand in under 3 minutes.

---

# 1. Product Context

We are building a travel-packing system that converts real physical objects into a computational scene and then projects the solution back into the real world.

Core product flow:

```text
real suitcase + real travel objects
              ↓
      iPhone RGB + LiDAR
              ↓
       3D scene reconstruction
              ↓
      digital twin / geometry
              ↓
       packing search algorithm
              ↓
      deterministic physics validation
              ↓
        candidate packing plan
              ↓
       PAN WORLD MODEL LAYER
              ↓
 counterfactual execution simulation
              ↓
   selected / explained action sequence
              ↓
       3D packing visualization
              ↓
        AR placement guidance
```

The deterministic geometry and physics layers remain authoritative for:

- metric distances,
- collisions,
- containment,
- support,
- static stability.

PAN is NOT a replacement for the physics engine.

PAN is the learned **world-simulation / simulative-reasoning layer**.

---

# 2. PAN Ground Truth

PAN is IFM's next-generation world model for embodied reasoning and physical-world simulation.

The published PAN work describes an **interactive, action-conditioned world model**:

```text
visual history / current world state
+
natural-language action
              ↓
             PAN
              ↓
predicted future visual world states
```

PAN is designed for:

- action-conditioned simulation,
- interactive world evolution,
- multi-step / long-horizon rollouts,
- simulative reasoning about possible futures.

It predicts future world states visually rather than returning exact metric rigid-body state.

Therefore:

> Use PAN to imagine the consequences of candidate packing actions.

Do NOT claim that PAN provides exact collision geometry, guaranteed Newtonian simulation, or centimeter-accurate physical predictions unless the actual HackCMU PAN interface explicitly exposes those capabilities.

References for the technical framing:

- PAN paper: https://arxiv.org/abs/2511.09057
- IFM PAN: https://ifm.ai/pan/
- IFM describes PAN as a next-generation foundation model for embodied reasoning and physical-world simulation.

---

# 3. Core Hackathon Thesis

Our project should not be:

```text
scan suitcase
→ solve bin packing
→ AR overlay
```

That is useful, but conventional.

The PAN-enhanced project becomes:

> **A system that perceives a traveler's physical packing problem, computes geometrically valid solutions, then uses a world model to simulate and reason about how those solutions would actually unfold when executed in the real world.**

The unique split is:

```text
GEOMETRIC ENGINE
"What placements are possible?"

PHYSICS VALIDATOR
"Is this candidate statically feasible?"

PAN WORLD MODEL
"What might happen if a human actually performs this action sequence?"
```

That distinction must survive implementation and judging.

---

# 4. The Main PAN Feature: Counterfactual Packing Rollouts

The primary PAN integration is:

## "Imagine Before You Pack"

Given the current suitcase state and a proposed next action:

```text
"Place the left shoe heel-first into the back-right corner,
rotated approximately 35 degrees clockwise."
```

PAN generates a short predicted future rollout of the scene.

We use this to create **counterfactual futures**:

```text
CURRENT STATE
      │
      ├──── Action A: place shoe first
      │          ↓
      │       PAN rollout A
      │
      ├──── Action B: place toiletry bag first
      │          ↓
      │       PAN rollout B
      │
      └──── Action C: place camera first
                 ↓
              PAN rollout C
```

The deterministic solver may say all three sequences eventually fit.

PAN helps us explore the **execution consequences** of those actions:

- does the action appear physically awkward?
- does the placement obstruct later access?
- does an object shift or topple?
- does the user's hand lose access to a later placement region?
- does the scene evolve differently than the idealized digital twin?
- is the action visually ambiguous or difficult to execute?

This is the main research / sponsor-track angle.

---

# 5. PAN Must Affect Product Behavior

Do NOT implement PAN merely as:

```text
"Generate a cool packing video"
```

That is a weak sponsor integration.

PAN must influence at least one meaningful output.

Preferred hierarchy:

## Level 1 — Required

PAN generates the visual future for a candidate action.

The user can inspect:

```text
PLAN
vs.
PAN-PREDICTED EXECUTION
```

## Level 2 — Strong

For the top K candidate next actions, PAN produces K counterfactual rollouts.

The application exposes them to the ranking / planning layer.

## Level 3 — Winning Stretch

A vision-based evaluator analyzes the PAN rollout and generates structured risk signals such as:

```json
{
  "accessibility_risk": 0.72,
  "visible_shift": true,
  "possible_topple": false,
  "occlusion_risk": 0.48,
  "confidence": 0.61
}
```

These signals do NOT override deterministic collision constraints.

They can affect action-order ranking:

```text
final action cost
=
packing heuristic
+
physics penalty
+
PAN execution-risk penalty
```

This turns PAN into a real planning component.

---

# 6. Important Scientific Boundary

The system must explicitly separate:

```text
hard constraints
```

from:

```text
learned predictions
```

## Hard / deterministic

- object intersection,
- suitcase boundaries,
- OBB / mesh collision,
- static support,
- known orientation constraints.

## Learned / uncertain

- likely movement,
- human execution difficulty,
- visual obstruction,
- likely object shifting,
- realistic scene evolution,
- counterfactual visual outcome.

Never claim:

> "PAN proves this packing arrangement is physically valid."

Instead say:

> "Our deterministic geometry verifies feasibility, while PAN provides a learned simulation of how the packing action may unfold in the real world."

That line is important for technical judges.

---

# 7. Input Representation to PAN

PAN works on visual world state plus natural-language action.

Our internal state is much richer:

```text
LiDAR geometry
3D object meshes
object transforms
packing plan
AR world coordinates
```

We need a bridge.

Implement a **PAN Scene Adapter**.

Conceptually:

```text
current reconstructed scene
        ↓
select canonical camera viewpoint
        ↓
render RGB scene OR use actual iPhone RGB frame
        ↓
generate action description
        ↓
PAN request
```

Support two possible image sources:

## Preferred A — Real camera frame

Use the latest RGB frame from the iPhone scan.

Advantages:

- real-world textures,
- strongest connection to actual environment,
- best sponsor-demo narrative.

## Preferred B — Rendered digital twin

Render a deterministic perspective frame from the reconstructed 3D scene.

Advantages:

- controlled camera,
- repeatable tests,
- easier development without iPhone connected.

Build both if practical.

The adapter should expose something conceptually like:

```python
pan_input = build_pan_input(
    scene=current_scene,
    action=packing_action,
    viewpoint=viewpoint
)
```

---

# 8. Action Prompt Generation

Do not send vague prompts like:

```text
"pack the suitcase"
```

Generate grounded, structured natural-language actions from the solver result.

Example:

```text
A traveler reaches for the black running shoe positioned
to the left of the suitcase and places it heel-first into
the rear-right corner of the open suitcase. The shoe is
rotated approximately 35 degrees clockwise and laid flat.
The other objects remain in their current positions.
```

The action generator should derive this from:

```text
object ID
semantic label if known
current pose
target pose
rotation
container region
action order
```

Keep prompt construction deterministic and debuggable.

---

# 9. Multi-Step Rollout

PAN's conceptual strength is interactive / long-horizon world simulation.

If the available HackCMU interface supports iterative continuation, implement:

```text
state_0
  ↓ action_1
PAN
  ↓
state_1
  ↓ action_2
PAN
  ↓
state_2
  ↓ action_3
...
```

This should preview a packing sequence rather than just one isolated action.

However:

- do NOT block the MVP on long-horizon rollout,
- one-step prediction is enough for initial integration,
- only chain outputs if the real API supports it cleanly.

---

# 10. Do Not Invent the PAN API

This is critical.

Public information confirms PAN's capabilities, but the HackCMU-specific access interface may be provided through:

- workshop documentation,
- environment variables,
- starter repositories,
- SDKs,
- Discord,
- sponsor instructions,
- hosted endpoint,
- notebook,
- local package.

Before implementing a client:

1. inspect the repository,
2. inspect README/docs,
3. inspect environment variables WITHOUT printing secrets,
4. search existing sponsor starter files,
5. inspect any HackCMU workshop materials available locally,
6. inspect official IFM documentation if network access is available.

Do NOT hallucinate:

```text
https://api.ifm.ai/pan/generate
```

or any other endpoint.

If PAN credentials/access are unavailable, implement a clean adapter with a mock backend so the application can integrate immediately once real access is known.

Example architecture:

```text
PanWorldModel
    ├── RealPanBackend
    └── MockPanBackend
```

---

# 11. Required Adapter Interface

Create a narrow interface.

Conceptually:

```python
class WorldModel:
    def simulate(
        self,
        observation,
        action,
        history=None,
        options=None
    ) -> SimulationResult:
        ...
```

Result should normalize whatever PAN returns:

```python
SimulationResult(
    video_path=...,
    final_frame_path=...,
    metadata=...,
    latency_ms=...,
    backend="pan"
)
```

The rest of the application should not depend directly on PAN SDK-specific types.

---

# 12. Failure-Tolerant Architecture

Sponsor APIs fail.

Hackathon Wi-Fi fails.

Inference may be slow.

Therefore PAN must be asynchronous from the core packing solver.

Pipeline:

```text
packing solver
      ↓
valid solution available immediately
      ↓
3D + AR path still works
      ↓
PAN simulation requested
      ↓
when ready:
attach rollout to candidate
```

The application should NEVER become unusable because PAN inference is unavailable.

Support:

```text
PAN status:
- pending
- complete
- failed
- unavailable
```

Cache completed rollouts.

Never rerun expensive inference unnecessarily.

---

# 13. Demo Strategy

The live demo should make PAN obvious.

Do NOT hide it behind a button nobody understands.

Recommended sequence:

### Step 1

Physically show:

```text
open suitcase
+
random travel objects
```

### Step 2

Scan.

Show:

```text
real world
→ reconstructed 3D digital twin
```

### Step 3

Generate candidate packing solution.

Show 3D animation.

### Step 4

Click:

```text
IMAGINE EXECUTION
```

or similar.

PAN displays a predicted world rollout of the next packing action.

### Step 5

Show two alternatives:

```text
WORLD A
shoe first

WORLD B
camera first
```

### Step 6

Explain:

> "The geometric solver tells us both plans fit. PAN lets us simulate how those actions may actually unfold before the traveler commits to them."

### Step 7

Select the plan and switch to AR guidance.

This ties:

```text
perception
→ reasoning
→ world simulation
→ action
```

into one coherent loop.

---

# 14. Judge-Friendly Technical Story

The demo explanation should approximately be:

> "LiDAR gives us metric geometry of the traveler's actual objects and suitcase."

> "Our packing engine searches over candidate arrangements, and our deterministic physics layer rejects collisions and unstable layouts."

> "But geometric feasibility is not the same as real-world executability."

> "We integrate IFM PAN as a learned world model. For candidate actions, we condition PAN on the current scene and a grounded physical action, then simulate possible future world states."

> "That gives us a counterfactual execution layer between planning and AR."

> "The final validated plan is projected back into the real world so the traveler can execute it."

That is the narrative every implementation choice should support.

---

# 15. Parallel Subagent Plan

Launch at least the following **Sonnet 5 Medium** subagents immediately.

---

## Subagent 1 — PAN Access Reconnaissance

Goal:

Determine the ACTUAL available PAN interface.

Inspect:

- repository,
- workshop starter code,
- docs,
- environment variables,
- installed packages,
- sponsor materials,
- official IFM resources.

Return:

1. exact authentication mechanism,
2. endpoint / SDK if available,
3. supported input types,
4. output types,
5. supported video/image dimensions,
6. action-conditioning syntax,
7. continuation / history support,
8. latency expectations,
9. rate limits if documented,
10. minimal verified request.

DO NOT reveal secret values.

DO NOT invent undocumented APIs.

If access cannot be found, state that explicitly and prepare the adapter for later configuration.

---

## Subagent 2 — World Model Adapter

Build the provider-neutral interface:

```text
WorldModel.simulate(...)
```

Implement:

```text
MockPanBackend
```

immediately.

If Subagent 1 finds verified PAN access, implement:

```text
RealPanBackend
```

Requirements:

- timeouts,
- structured errors,
- request IDs,
- caching,
- retry policy where appropriate,
- no secrets in logs.

Write tests using mocks.

---

## Subagent 3 — Scene → PAN Observation Adapter

Build the visual observation pipeline.

Support:

### Mode A
real iOS RGB frame

### Mode B
rendered 3D digital-twin frame

Requirements:

- canonical image sizing,
- deterministic viewpoint metadata,
- optional crop,
- scene ID,
- timestamp,
- mapping back to the source scene.

Provide fixtures so PAN can be tested without an iPhone.

---

## Subagent 4 — Packing Action Language Generator

Translate structured solver output into grounded PAN actions.

Input:

```text
current scene
object
target pose
rotation
order
```

Output:

```text
natural-language physical action
```

Requirements:

- deterministic,
- concise,
- grounded,
- no invented object attributes,
- human-readable,
- unit-tested.

Example:

```text
Place the black shoe heel-first into the rear-right
corner of the suitcase and rotate it approximately
35 degrees clockwise while keeping it flat.
```

---

## Subagent 5 — Counterfactual Rollout Manager

Build:

```text
simulate_candidate_actions(...)
```

Given top K candidate actions:

```text
A
B
C
```

launch / queue PAN simulations and associate results with each candidate.

Implement:

- parallel requests if allowed,
- caching,
- state tracking,
- cancellation,
- latency logging,
- candidate/result IDs.

Do not allow PAN latency to block deterministic packing.

---

## Subagent 6 — Rollout Evaluation / Risk Extraction

Build a first-pass mechanism for extracting useful signals from PAN output.

Do NOT pretend to recover exact physics.

Explore practical signals such as:

- large visible object displacement,
- obvious topple,
- blocking / occlusion of target region,
- significant deviation from expected final visual arrangement,
- inability to see / access the next object.

Use the lightest reliable mechanism available in the repository.

Possible architecture:

```text
PAN rollout
     ↓
sample frames
     ↓
vision evaluator
     ↓
structured execution-risk metadata
```

If no reliable evaluator is available within hackathon time, provide:

- side-by-side rollout visualization,
- human-selectable candidate comparison,
- hooks for later automated scoring.

Be rigorous about uncertainty.

---

## Subagent 7 — Demo + Integration Harness

Create a deterministic demo scene:

```text
carry-on suitcase
shoe
camera
laptop
toiletry case
charger
headphones
```

Create at least two candidate packing sequences.

The harness must be able to run:

```text
scene
↓
candidate actions
↓
physics validation
↓
PAN simulation
↓
candidate comparison
↓
3D / AR handoff
```

Even when real PAN is unavailable, the same pipeline must work with MockPanBackend.

---

# 16. Orchestrator Responsibilities

While agents execute, independently inspect the repository.

When agents return:

1. reconcile their interfaces,
2. enforce one scene / object ID convention,
3. keep PAN adapter isolated,
4. ensure no secret leakage,
5. integrate one end-to-end flow,
6. test failure modes,
7. cache expensive outputs,
8. verify core packing still works with PAN disabled,
9. make PAN-visible UI hooks easy for rendering teammate,
10. write a concise technical README,
11. commit working changes.

Do not accept subagent work blindly.

---

# 17. Integration With Physics Branch

The deterministic physics engine should expose something like:

```python
physics = validate_layout(scene, candidate)
```

Only candidates that satisfy required hard constraints should normally enter PAN simulation.

Pipeline:

```text
candidate generated
        ↓
physics valid?
   ┌────┴────┐
  no         yes
  ↓           ↓
reject      PAN rollout
```

Why:

PAN inference is expensive and probabilistic.

Do not spend world-model calls evaluating layouts already known to be impossible.

---

# 18. Integration With Packing Solver

Ask the packing solver for:

```text
top K candidate NEXT ACTIONS
```

rather than only one finished layout, if feasible.

Recommended K for hackathon:

```text
K = 2 or 3
```

Enough to demonstrate counterfactual reasoning without exploding inference cost.

Example:

```text
Candidate A:
shoe → laptop → camera

Candidate B:
laptop → shoe → camera

Candidate C:
camera → shoe → laptop
```

PAN can simulate the immediate next action for each branch.

If long-horizon continuation works, extend further.

---

# 19. World-Model-Aware Score

Do NOT let PAN override hard constraints.

If structured rollout evaluation becomes reliable enough, use:

```text
candidate_score =
geometry_score
+ stability_score
+ execution_risk_weight * pan_risk
```

Where:

```text
pan_risk
```

is explicitly probabilistic.

Log every component separately.

Never collapse everything into an opaque "AI score."

---

# 20. UI / Renderer Contract

Provide the visualization teammate enough data to show:

```text
Candidate A
physics: valid
PAN: complete
execution risk: low

Candidate B
physics: valid
PAN: complete
execution risk: medium
```

And assets:

```text
pan_preview_video
pan_final_frame
action_text
simulation_status
risk_metadata
```

This should make PAN visible without the renderer needing to know its API.

---

# 21. Travel-Track Relevance

Everything should connect back to travel.

The user story is:

> "I am packing for a trip. I do not want to manually measure everything, spend twenty minutes playing suitcase Tetris, or discover halfway through that the packing order is awkward."

Our system:

```text
sees
→ plans
→ imagines
→ guides
```

This is stronger than a generic optimizer because the product operates on the traveler's actual physical environment.

---

# 22. IFM / PAN Prize Relevance

The PAN integration should demonstrate the exact quality that makes world models interesting:

> reasoning about possible future states before acting.

The strongest sponsor explanation is:

> "We don't use PAN as a chatbot. We use it as a predictive world model between planning and action."

If the implementation supports actual interactive continuation:

> "Each packing action changes the world state, and PAN can roll the world forward again from the resulting state."

That should be emphasized.

---

# 23. Hackathon Scope Discipline

P0:

1. discover real PAN access,
2. provider-neutral adapter,
3. mock backend,
4. scene → image adapter,
5. action generator,
6. one real PAN rollout,
7. show rollout in demo UI.

P1:

8. simulate 2–3 candidate actions,
9. caching,
10. asynchronous execution,
11. candidate comparison.

P2:

12. automatic rollout risk extraction,
13. multi-step PAN continuation,
14. world-model-aware candidate ranking.

Do NOT sacrifice the core travel demo for P2.

---

# 24. Evaluation / Evidence

Create a tiny demo benchmark.

Use 3–5 predefined scenes / action pairs.

For each, record:

```text
input scene
action
PAN output
latency
human judgment:
- plausible?
- preserves object identities?
- action followed?
- major physical inconsistency?
```

Do NOT fabricate quantitative model accuracy.

This is a hackathon prototype, not a clinical or robotics benchmark.

The purpose is to show that we evaluated the integration rather than trusting one cherry-picked rollout.

---

# 25. Security / Credentials

Never:

- print API keys,
- commit credentials,
- log Authorization headers,
- put tokens in demo screenshots.

Use environment variables and `.env.example`.

If sponsor tooling provides a token, treat it as secret.

---

# 26. Git Discipline

Before work:

```bash
git status
git branch --show-current
```

Prefer a dedicated branch such as:

```text
pan-integration
```

unless the team already specified another branch.

Do not overwrite teammates' work.

Suggested commits:

```text
pan: add world model provider interface
pan: add scene observation adapter
pan: generate grounded packing actions
pan: integrate PAN action simulation
pan: add counterfactual rollout manager
pan: expose simulation assets to renderer
pan: add PAN demo fixtures and tests
```

---

# 27. Definition of Done

Minimum winning-path integration:

```text
real / mocked travel scene
        ↓
valid packing candidate
        ↓
current RGB observation
        +
grounded packing action
        ↓
PAN
        ↓
future world rollout
        ↓
display next to geometric plan
        ↓
AR execution
```

The integration is not done if PAN is only mentioned in the README.

The integration is done when the live demo can visibly answer:

> **"What might happen if I execute this packing action?"**

using PAN.

---

# 28. Final Demo Line

The final technical narrative should land on:

> **"Our solver tells us what fits. Our physics layer tells us what is geometrically feasible. PAN lets the system imagine what happens when the traveler actually acts. Then AR turns that predicted and validated plan back into physical guidance."**

Begin now.

First:

1. inspect the repository,
2. launch the Sonnet 5 Medium subagents in parallel,
3. determine the real HackCMU PAN access path,
4. implement the provider-neutral world-model adapter and mock backend immediately,
5. integrate one complete candidate-action rollout,
6. test it,
7. commit it.

Do not stop at architecture notes. Ship the integration.
