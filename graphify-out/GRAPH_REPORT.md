# Graph Report - HACKCMU2026  (2026-09-12)

## Corpus Check
- 117 files · ~140,554 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1639 nodes · 4486 edges · 72 communities (62 shown, 8 thin omitted)
- Extraction: 92% EXTRACTED · 8% INFERRED · 0% AMBIGUOUS · INFERRED: 361 edges (avg confidence: 0.9)
- Token cost: 1,647,225 input · 0 output

## Community Hubs (Navigation)
- PAN Rollout Ranking
- OBB Collision (SAT)
- Scene Metrics & Constraints
- PAN Observation Rendering
- packer3d Bounds & Balance
- packer3d Container Model
- OBB Geometry Core
- PAN Action-Language Generator
- Support Contact Checking
- packer3d Edge-Case Tests
- Physics Validator Pipeline
- PAN World-Model Rendering
- iOS LiDAR Scan App (Swift)
- PAN Region/Orientation Phrasing
- Constraint Tilt Checks
- PAN Evaluate Tests
- packer3d Item & Placement
- Containment Checking
- Physics Stress Tests
- PAN Observation Building
- packer3d Input Validation
- Physics Test Fixtures
- Support Convex-Hull Math
- packer3d Decoder (PackState)
- PAN Evaluate Frame Diffing
- Compressibility Model
- PAN World-Model Tests
- AR Packing App Architecture
- Mock PAN Backend
- PAN Result Caching
- Integration & Physics Docs
- Fixture: Collision Scene
- Fixture: Valid Packed Scene
- WorldModel Interface
- PAN CLI & Real Backend
- Physics CLI
- Physics JSON I/O
- Fixture: Nearly Full Scene
- PAN Access Reconnaissance
- MVP Feature List
- Fixture: Floating Object
- Fixture: Wall Penetration
- Scene Round-Trip Tests
- Physics validate() & CLI Docs
- Scan Output Spec
- Fixture: Object Touching Wall
- Label Humanization
- Fixture: Oversized Object
- Fixture: Rotated Object in Corner
- Fixture: Stacked Objects
- Fixture: Precarious Balance
- Fixture: Soft Item Compression
- apply_placements Tests
- ScannedItem Adapter
- BoxFit Quaternion Tests
- Physics CLI Tests
- packer3d Dragon Capsule Renders
- packer3d Debug Visualizer
- PRD Demo & Architecture
- Overview Build Plan & Risks
- Project Scope Docs
- packer3d Coordinate System
- packer3d Obstacle Model
- MVP Team & Contract
- packer3d README Highlights
- PAN Package Init
- Guideline: Simplicity First
- Guideline: Think Before Coding
- packer3d Suitcase Render
- packer3d Package Metadata

## God Nodes (most connected - your core abstractions)
1. `Scene` - 126 edges
2. `Object` - 92 edges
3. `Container` - 83 edges
4. `pack_optimized()` - 83 edges
5. `PackingAction` - 57 edges
6. `Container` - 57 edges
7. `validate_layout()` - 57 edges
8. `obb_from()` - 56 edges
9. `precompute()` - 53 edges
10. `SimulationRequest` - 50 edges

## Surprising Connections (you probably didn't know these)
- `Centre-of-Mass Model (first-order approximation)` --semantically_similar_to--> `scene_metrics()`  [INFERRED] [semantically similar]
  packer3d/ALGORITHM.md → physics/metrics.py
- `PAN Eval Log: Scene/Action Pairs (mock rollouts)` --references--> `run_demo()`  [EXTRACTED]
  docs/pan_eval_log.md → pan/demo.py
- `Failure Tolerance (pending/unavailable, CachingWorldModel dedup)` --references--> `RolloutManager`  [EXTRACTED]
  docs/PAN_INTEGRATION.md → pan/rollouts.py
- `Connecting LiDAR Heightmap Output (§8)` --references--> `object_from_scanned_item()`  [EXTRACTED]
  packer3d/ALGORITHM.md → physics/io.py
- `Travel Constraint Layer (physics/constraints.py, transitive load propagation)` --conceptually_related_to--> `scene_metrics()`  [EXTRACTED]
  docs/PHYSICS.md → physics/metrics.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Four-Person Team Role Split (iOS/LiDAR, rendering/AR, packing algorithm, physics validation)** — mvp_team_split, prd_team_ownership, prd_system_architecture [EXTRACTED 1.00]
- **Physics Validation Pipeline (precompute -> containment -> collision -> support -> constraints -> metrics)** — docs_physics_validation_layer, physics_scene_geometry_precompute, docs_physics_containment_check, docs_physics_collision_sat, docs_physics_support_stability, docs_physics_constraints_travel, physics_metrics_scene_metrics [EXTRACTED 1.00]
- **PAN Counterfactual Simulation Flow (physics gate -> world model backend -> evaluator -> reports -> eval log)** — pan_rollouts_physics_gate, pan_world_model_get_world_model, pan_evaluate_evaluate_rollout, docs_pan_eval_log_scene_action_pairs [EXTRACTED 1.00]
- **** — packer3d_examples_dragon_naive_packing_result, packer3d_examples_dragon_naive_naive_strategy, packer3d_examples_dragon_naive_utilization_metric, packer3d_examples_dragon_naive_center_of_mass [INFERRED 0.75]
- **** — pan_fixtures_scene_nearly_full_front_high_laptop, pan_fixtures_scene_nearly_full_front_high_shoe, pan_fixtures_scene_nearly_full_front_high_camera, pan_fixtures_scene_nearly_full_front_high_charger, pan_fixtures_scene_nearly_full_front_high_toiletry_bottle, pan_fixtures_scene_nearly_full_front_high_headphones_case, pan_fixtures_scene_nearly_full_front_high_toiletry_bag [EXTRACTED 1.00]
- **** — pan_fixtures_scene_object_touching_wall_front_high_container, pan_fixtures_scene_object_touching_wall_front_high_shoe, pan_fixtures_scene_object_touching_wall_front_high_laptop [INFERRED 0.75]
- **** — pan_fixtures_scene_object_touching_wall_overhead_45_container, pan_fixtures_scene_object_touching_wall_overhead_45_laptop, pan_fixtures_scene_object_touching_wall_overhead_45_shoe [INFERRED 0.85]
- **** — pan_fixtures_scene_oversized_object_front_high, pan_fixtures_oversized_duffel, pan_fixtures_container_bin [INFERRED 0.85]
- **** — pan_fixtures_scene_oversized_object_overhead_45, pan_fixtures_scene_oversized_object_overhead_45_oversized_duffel, pan_fixtures_scene_oversized_object_overhead_45_container_bin [INFERRED 0.85]
- **Corner-rotation packing test scenario** — pan_fixtures_scene_rotated_object_in_corner_front_high_container, pan_fixtures_scene_rotated_object_in_corner_front_high_shoe, pan_fixtures_scene_rotated_object_in_corner_front_high_toiletry_bag [INFERRED 0.85]
- **** — pan_fixtures_scene_rotated_object_in_corner_overhead_45_container, pan_fixtures_scene_rotated_object_in_corner_overhead_45_shoe, pan_fixtures_scene_rotated_object_in_corner_overhead_45_toiletry_bag [EXTRACTED 1.00]
- **pan_fixtures_scene_stacked_objects_front_high_stack_group** — pan_fixtures_scene_stacked_objects_front_high_base_container, pan_fixtures_scene_stacked_objects_front_high_toiletry_bag, pan_fixtures_scene_stacked_objects_front_high_headphones_case [INFERRED 0.85]
- **** — pan_fixtures_scene_stacked_objects_overhead_45_container, pan_fixtures_scene_stacked_objects_overhead_45_headphones_case, pan_fixtures_scene_stacked_objects_overhead_45_toiletries_bag [INFERRED 0.85]
- **** — pan_fixtures_scene_with_collision_front_high_charger, pan_fixtures_scene_with_collision_front_high_toiletry_bottle, pan_fixtures_scene_with_collision_front_high_headphones_case [INFERRED 0.75]
- **** — pan_fixtures_scene_with_collision_overhead_45_charger, pan_fixtures_scene_with_collision_overhead_45_toiletry_bottle, pan_fixtures_scene_with_collision_overhead_45_headphones_case [INFERRED 0.75]
- **** — pan_fixtures_scene_with_collision_overhead_45_headphones_case, pan_fixtures_scene_with_collision_overhead_45_camera, pan_fixtures_scene_with_collision_overhead_45_toiletry_bag [INFERRED 0.75]
- **** — pan_fixtures_scene_with_floating_object_front_high_bin, pan_fixtures_scene_with_floating_object_front_high_shoe, pan_fixtures_scene_with_floating_object_front_high_laptop, pan_fixtures_scene_with_floating_object_front_high_charger [EXTRACTED 1.00]
- **** — pan_fixtures_scene_with_floating_object_overhead_45_shoe, pan_fixtures_scene_with_floating_object_overhead_45_laptop, pan_fixtures_scene_with_floating_object_overhead_45_charger [INFERRED 0.75]
- **** — pan_fixtures_scene_with_precarious_balance_front_high_container, pan_fixtures_scene_with_precarious_balance_front_high_toiletry_bag, pan_fixtures_scene_with_precarious_balance_front_high_camera [INFERRED 0.75]
- **** — pan_fixtures_scene_with_precarious_balance_overhead_45_container, pan_fixtures_scene_with_precarious_balance_overhead_45_toiletry_bag, pan_fixtures_scene_with_precarious_balance_overhead_45_camera [INFERRED 0.75]
- **** — pan_fixtures_scene_with_soft_item_compression_front_high_container, pan_fixtures_scene_with_soft_item_compression_front_high_clothes_bag, pan_fixtures_scene_with_soft_item_compression_front_high_toiletry_bag [INFERRED 0.75]
- **Overhead-45 compression scene grouping container, clothes item, and toiletry bag item** — pan_fixtures_scene_with_soft_item_compression_overhead_45, pan_fixtures_scene_with_soft_item_compression_container, pan_fixtures_scene_with_soft_item_compression_clothes_item, pan_fixtures_scene_with_soft_item_compression_toiletry_bag_item [EXTRACTED 1.00]
- **** — pan_fixtures_scene_with_wall_penetration_front_high_container, pan_fixtures_scene_with_wall_penetration_front_high_shoe, pan_fixtures_scene_with_wall_penetration_front_high_laptop [INFERRED 0.85]
- **** — pan_fixtures_scene_with_wall_penetration_overhead_45_laptop, pan_fixtures_scene_with_wall_penetration_overhead_45_shoe, pan_fixtures_scene_with_wall_penetration_overhead_45_container [INFERRED 0.85]
- **** — pan_fixtures_valid_packed_scene_front_high_container, pan_fixtures_valid_packed_scene_front_high_laptop, pan_fixtures_valid_packed_scene_front_high_shoe, pan_fixtures_valid_packed_scene_front_high_charger [INFERRED 0.75]
- **** — pan_fixtures_valid_packed_scene_front_high_container, pan_fixtures_valid_packed_scene_front_high_toiletry_bottle, pan_fixtures_valid_packed_scene_front_high_headphones_case, pan_fixtures_valid_packed_scene_front_high_toiletry_bag [INFERRED 0.75]
- **** — pan_fixtures_valid_packed_scene_overhead_45_container, pan_fixtures_valid_packed_scene_overhead_45_laptop, pan_fixtures_valid_packed_scene_overhead_45_shoe, pan_fixtures_valid_packed_scene_overhead_45_toiletry_bag [INFERRED 0.75]
- **** — pan_fixtures_valid_packed_scene_overhead_45_camera, pan_fixtures_valid_packed_scene_overhead_45_headphones_case, pan_fixtures_valid_packed_scene_overhead_45_charger, pan_fixtures_valid_packed_scene_overhead_45_toiletry_bottle [INFERRED 0.65]

## Communities (72 total, 8 thin omitted)

### Community 0 - "PAN Rollout Ranking"
Cohesion: 0.05
Nodes (54): DescribeFn, EvaluateFn, Exception, Future, ObservationFn, packing_subset(), rank_candidate_rollups(), rank_candidates() (+46 more)

### Community 1 - "OBB Collision (SAT)"
Cohesion: 0.05
Nodes (41): aabb_overlap(), check_collision(), check_pairs(), collide_scene(), CollisionResult, ndarray, OBB-vs-OBB collision test via the Separating Axis Theorem (SAT). Assumptions…, Cheap world-axis-aligned bounding-box overlap test, for broad-phase pruning.… (+33 more)

### Community 2 - "Scene Metrics & Constraints"
Cohesion: 0.08
Nodes (40): Travel-semantics constraint checker. Optional metadata-driven layer on top of…, Scene-level packing metrics -- objective terms for the packing solver.…, scene_metrics(), check_no_duplicate_ids(), MalformedSceneError, on_floor(), precompute(), ndarray (+32 more)

### Community 3 - "PAN Observation Rendering"
Cohesion: 0.07
Nodes (50): Image, ImageDraw, _camera_basis(), canonicalize(), color_for_id(), _draw_container_faces(), _draw_ground(), _draw_object_edges() (+42 more)

### Community 4 - "packer3d Bounds & Balance"
Cohesion: 0.08
Nodes (50): Mass-swap balancing: exact, geometry-preserving centre-of-mass improvement.…, exhaustive_small(), gap_report(), lower_bounds(), _min_drop(), Provable bounds, gap reporting and exhaustive search for tiny instances. 3D bin…, Smallest k such that the sum of all but the k largest values is <= cap., Try every item sequence (best-orientation decoding) and return the best packing. (+42 more)

### Community 5 - "packer3d Container Model"
Cohesion: 0.07
Nodes (51): balance_masses(), Greedy best-improvement swaps; mutates ``placements`` in place. Returns #swaps., Container, The bin: a box or a vertical cylinder. ``min_support`` is the fraction of an…, load_scenario(), ``src`` = path, JSON string or dict. Returns (container, items,…, OptimizerConfig, test_determinism_with_iteration_budget() (+43 more)

### Community 6 - "OBB Geometry Core"
Cohesion: 0.08
Nodes (31): axis_projected_extent_m(), Full extent (meters) of `obb` projected onto world-space unit `axis`. Same…, OBB, obb_vertices(), Shared OBB geometry: quaternion -> world axes, half-extents, vertices. Every…, 8x3 array of world-space corner points, in `SIGNS` order., _clamp01(), _malformed() (+23 more)

### Community 7 - "PAN Action-Language Generator"
Cohesion: 0.09
Nodes (39): PAN Integration Architecture (solver -> physics gate -> observation -> action -> world model -> evaluator -> reports), candidates.json Data Contract for the Renderer, closing_phrase(), describe_action(), describe_sequence(), fill_action_text(), Deterministic action-language generator (PAN.md section 8). Turns a structured…, The fixed "other objects" sentence, plus an optional "will be placed next"… (+31 more)

### Community 8 - "Support Contact Checking"
Cohesion: 0.08
Nodes (31): layer_phrase(), What the object rests on, from a `check_support` result: resting on other…, _axis_rect(), check_support(), _clip(), _dedupe(), _line_isect(), _poly_area() (+23 more)

### Community 9 - "packer3d Edge-Case Tests"
Cohesion: 0.14
Nodes (47): pack_optimized(), assert_valid(), box_container(), _cube_mesh(), _cylinder_mesh(), _fragile_scenario(), packed_ids(), Edge-case suite for packer3d. Every packing produced here is re-checked with… (+39 more)

### Community 10 - "Physics Validator Pipeline"
Cohesion: 0.09
Nodes (19): Renderer-Relevant Violation Fields, Physics Gate Rejects Candidate C Before PAN Call (FRAGILE_OBJECT_OVERLOADED), PAN Eval Log: Scene/Action Pairs (mock rollouts), RolloutManager._physics_gate (pan/rollouts.py), _clamp01(), Run the full physics validation pipeline on `scene`. See module docstring., _sort_key(), validate_layout() (+11 more)

### Community 11 - "PAN World-Model Rendering"
Cohesion: 0.08
Nodes (41): _add_noise(), _arrival_blob_size(), _arrival_start(), _background_color(), _clamped_center(), _content_seed(), _decode_animated_bytes(), _decode_png_b64() (+33 more)

### Community 12 - "iOS LiDAR Scan App (Swift)"
Cohesion: 0.07
Nodes (35): AnchorEntity, App, ARKit, ARView, Binding, Bool, Codable, Context (+27 more)

### Community 13 - "PAN Region/Orientation Phrasing"
Cohesion: 0.07
Nodes (31): _combine_region(), current_location_phrase(), _local_offset(), _local_x_yaw_deg(), orientation_phrase(), currently in the <region> of the suitcase" if `position` is inside the…, Name of the container-footprint-thirds region containing `position`, projected…, Yaw (degrees) of the local +x axis in the world XZ plane, defined so that a… (+23 more)

### Community 14 - "Constraint Tilt Checks"
Cohesion: 0.12
Nodes (24): check_constraints(), ConstraintViolation, ConstraintWarning, ndarray, `_up_axis_tilt_deg`'s clipped cosine for every object at once. Dotting a local…, Angle in degrees between an object's local up axis and world up., _up_axis_cosines(), _up_axis_tilt_deg() (+16 more)

### Community 15 - "PAN Evaluate Tests"
Cohesion: 0.13
Nodes (24): evaluate_rollout(), Extract `RiskSignals` from one rollout, or None when there is nothing to read.…, act(), draw(), obs(), Tests for `pan.evaluate`: synthetic Pillow frames -> RiskSignals. Frames are…, (1) Red (acted) translates, blue stays -> no visible shift., (2) Blue also drifts 10 px -> visible_shift True. (+16 more)

### Community 16 - "packer3d Item & Placement"
Cohesion: 0.09
Nodes (34): Item, Placement, True volume (mesh volume if scanned, pi r^2 h for cylinders, else the box)., Where one item ended up. ``position`` is the MIN corner of the oriented…, A rectangular box or a cylinder to be packed. Use ``Item.box(...)`` /…, _item_local_rotation(), matrix_to_quaternion(), physics_container_dict() (+26 more)

### Community 17 - "Containment Checking"
Cohesion: 0.14
Nodes (20): _build_result(), check_containment(), check_scene_containment(), _containment_arrays(), ContainmentResult, ndarray, Object-in-container containment checks. Assumptions: - Both container and…, Assemble one ContainmentResult from one row of `_containment_arrays`'s output.… (+12 more)

### Community 18 - "Physics Stress Tests"
Cohesion: 0.10
Nodes (28): CollisionSymmetry, ContainmentSelfConsistency, Determinism, MalformedInputRejected, NoCrashesOnValidInput, NoOverlapUnderTranslation, Container, Random (+20 more)

### Community 19 - "PAN Observation Building"
Cohesion: 0.10
Nodes (21): build_pan_input(), observation_from_scene(), Any, Path, Mode B: render a deterministic RGB frame of `scene`. `timestamp` is fixed to…, Write `obs.image` as a PNG and record `image_path` on the observation., Pack a scene + already-described action into a `SimulationRequest`. Uses…, save_observation() (+13 more)

### Community 20 - "packer3d Input Validation"
Cohesion: 0.10
Nodes (22): is_finite_number(), _check_dims(), _check_positive(), _check_target(), Build an item from a lidar measurement: bounding dims (length, depth, height) +…, Build an item from a scanned mesh (``vertices`` (N,3), optional triangle…, Build an item from the LiDAR spike's scan JSON (see ``SCAN_OUTPUT.md``):…, _item_from_dict() (+14 more)

### Community 21 - "Physics Test Fixtures"
Cohesion: 0.11
Nodes (28): Cheap solver feedback for scoring a candidate position -- does not run the…, Object, _base_objects(), _container(), _quat_y(), Hand-computed synthetic Scenes for HackCMU 2026 (Travel track) physics tests.…, Same 7 items as valid_packed_scene, but shoe is shifted -0.025m in Z so it…, laptop pushed through the -X wall by exactly 0.03m; shoe stays valid. laptop… (+20 more)

### Community 22 - "Support Convex-Hull Math"
Cohesion: 0.09
Nodes (26): _cross(), _face_hull(), _hull(), half(), ndarray, Convex hull of XZ points (monotone chain), CCW, collinear points dropped.…, `_hull` of the 4 corners of one box face, without the monotone chain. `xz` is…, _xz_hull() (+18 more)

### Community 23 - "packer3d Decoder (PackState)"
Cohesion: 0.11
Nodes (12): NamedTuple, PackState, ndarray, Coordinate of the container wall when projecting point p along -axis a., Vectorised feasibility and placement score for candidate min-corners ``lo``…, Best feasible candidate with score < ``best`` (branch-and-bound over sorted…, Place ``item``; ``orient_idx`` = preferred orientation (others tried if it…, Incremental packing state: solids (obstacles + placed items), extreme points,… (+4 more)

### Community 24 - "PAN Evaluate Frame Diffing"
Cohesion: 0.11
Nodes (26): compose_side_by_side(), _evaluate_segmented(), _evaluate_unsegmented(), _largest_blob_bbox(), _merged(), _noise(), Any, ndarray (+18 more)

### Community 25 - "Compressibility Model"
Cohesion: 0.15
Nodes (16): SAT Collision Detection (physics/collision.py), Travel Constraint Layer (physics/constraints.py, transitive load propagation), Containment Check (physics/containment.py), Support and Static Stability (physics/support.py, convex-hull contact polygons, chain instability), combined_collision_allowance_m(), compression_allowance_m(), container_wall_allowance_m(), ndarray (+8 more)

### Community 26 - "PAN World-Model Tests"
Cohesion: 0.15
Nodes (15): _blob(), _make_image(), MockRenderedRolloutTests, height(), _put_the_shoe_back(), ndarray, Offline unittest coverage for pan.world_model (Mock, Real, Caching, factory)., The mock against real rendered observations -- the regime the demo runs in, and… (+7 more)

### Community 27 - "AR Packing App Architecture"
Cohesion: 0.09
Nodes (25): PAN Integration Thesis (physics gates, PAN imagines outcome), Capture -> Geometry -> Solver -> Guidance Architecture, Capture Stage (bag catalog/scan, item OBB), Depth Sensing Is Coarse (constraint + mitigation), Item Geometry Model (obb, rigidity, k, orientation_lock, access_priority, nestable_volume), Guidance (anchoring, RealityKit rendering, step mode, layer view), Most Contents Aren't Rigid (compressibility factor k), Solver (Nest/Order/Place/Search/Fill passes, biased random-key GA) (+17 more)

### Community 28 - "Mock PAN Backend"
Cohesion: 0.26
Nodes (9): SimulationRequest, MockPanBackend, Offline stand-in for PAN. Deterministic given (frame0, action.text, options):…, _clean_pan_env(), _make_action(), _make_observation(), MockDeterminismTests, Ensure none of the PAN_* env vars leak in from the real environment. (+1 more)

### Community 29 - "PAN Result Caching"
Cohesion: 0.16
Nodes (9): ndarray, Normalized output of any backend. `frames` may be empty on failure., SimulationResult, CachingWorldModel, Wraps any WorldModel with an on-disk cache of completed rollouts. Key =…, CachingWorldModelTests, simulate(), _CountingFakeBackend (+1 more)

### Community 30 - "Integration & Physics Docs"
Cohesion: 0.12
Nodes (18): Surgical Changes, Integration Pipeline (iOS scan -> JSON -> validate -> solver loop -> renderer), Scene JSON Shape (scene_to_dict/scene_from_dict), OBB Geometry Representation (physics/geometry.py, obb_from), Physics Validation Layer — Technical Reference (v2), Schema Facts (units meters, ScannedItem cm exception, quaternion rotation), AR Packing Assistant (MVP demo spec), AR Packing Assistant — Project Overview (+10 more)

### Community 31 - "Fixture: Collision Scene"
Cohesion: 0.18
Nodes (16): scene_with_collision_front_high.png (rendered test fixture, front-high camera), packing bin/container (translucent blue box), charger (small cyan/green box, labeled 'charger'), headphones_case (purple box, labeled 'headphones_case'), laptop (brown box, labeled 'laptop'), shoe (labeled 'shoe', overlapping laptop box outline), toiletry_bag (teal box, labeled 'toiletry_bag'), toiletry_bottle (yellow box, labeled 'toiletry_bottle') (+8 more)

### Community 32 - "Fixture: Valid Packed Scene"
Cohesion: 0.17
Nodes (17): valid_packed_scene_front_high.png (fixture image), charger (packed item), Packing container/case (translucent blue box), headphones_case (packed item), laptop (packed item), shoe (packed item), toiletry_bag (packed item), toiletry_bottle (packed item) (+9 more)

### Community 33 - "WorldModel Interface"
Cohesion: 0.15
Nodes (9): Failure Tolerance (pending/unavailable, CachingWorldModel dedup), The only interface the rest of the app depends on., WorldModel, get_world_model(), Write frame_00.png .. frame_NN.png + rollout.gif into `out_dir`. Returns a copy…, Pick a backend: "mock" | "pan" | "auto" (Real if available() else Mock).…, PathLike, Protocol (+1 more)

### Community 34 - "PAN CLI & Real Backend"
Cohesion: 0.21
Nodes (6): main(), CLI for the PAN demo pipeline: `python3 -m pan demo` / `python3 -m pan status`., _status(), _backoff_seconds(), HTTP seam for the real PAN service. Configured from environment variables. This…, RealPanBackend

### Community 35 - "Physics CLI"
Cohesion: 0.33
Nodes (11): ArgumentParser, Namespace, `validate_layout`/`validate` result -> JSON string. Handles the numpy…, result_to_json(), build_parser(), _cmd_example(), _cmd_scan_to_object(), _cmd_validate() (+3 more)

### Community 36 - "Physics JSON I/O"
Cohesion: 0.32
Nodes (11): constraints_from_dict(), constraints_to_dict(), container_from_dict(), container_to_dict(), _floats(), object_from_dict(), object_to_dict(), Container (+3 more)

### Community 37 - "Fixture: Nearly Full Scene"
Cohesion: 0.27
Nodes (11): Scene: nearly full container (front-high view), Camera item (teal/dark box, labeled 'camera'), Front-high camera angle (evaluation viewpoint), Charger item (purple/magenta box, labeled 'charger'), Packing container (semi-transparent blue box), Headphones case item (small blue box, labeled 'headphones case'), Laptop item (brown box, labeled 'laptop'), Shoe item (labeled 'shoe', stacked with/near laptop) (+3 more)

### Community 38 - "PAN Access Reconnaissance"
Cohesion: 0.22
Nodes (10): Configuration Contract (PAN_API_KEY/PAN_BASE_URL/PAN_MODEL/PAN_TIMEOUT_S), Executive Answer: No Programmatic PAN Access Exists, Next Steps for Real PAN Access, PAN's Published Interface Shape (arXiv 2511.09057), PAN Access Reconnaissance, Human Judgment Table (to fill in after real PAN run), Access Status (no real PAN API/SDK exists), Honest Limitations (mock backend, visual risk proxies only, single viewpoint) (+2 more)

### Community 39 - "MVP Feature List"
Cohesion: 0.20
Nodes (10): 3D Digital Twin (feature), 3D Packing Simulation (feature), AR Placement Guide (feature), Core Loop (scan -> reconstruct -> pack -> simulate -> AR), Packing Solver (feature, MVP scope), Physics / Validation (feature, MVP scope), Spatial Scan (feature), packer3d Algorithm Documentation (+2 more)

### Community 40 - "Fixture: Floating Object"
Cohesion: 0.29
Nodes (9): packing bin/container, charger object (floating/unsupported), laptop object, shoe object, scene_with_floating_object_overhead_45.png (companion view), Charger object (yellow-green, floating unsupported), Packing container/bin (blue box, overhead-45 render), Laptop object (pink, stacked on shoe) (+1 more)

### Community 41 - "Fixture: Wall Penetration"
Cohesion: 0.33
Nodes (10): Scene: Wall Penetration (Front-High View), Container (blue box with translucent walls), Laptop object (thin pink/red slab, labeled 'laptop'), Shoe object (brown box, labeled 'shoe'), Wall penetration violation (invalid containment test case), Scene: Wall Penetration (Overhead 45), Blue container/bin, Laptop object (pink/magenta box) (+2 more)

### Community 42 - "Scene Round-Trip Tests"
Cohesion: 0.31
Nodes (5): scene_from_dict(), _fixture_funcs(), Every zero-arg Scene factory defined in tests/fixtures.py., TestResultToJson, TestSceneRoundTrip

### Community 43 - "Physics validate() & CLI Docs"
Cohesion: 0.28
Nodes (6): physics CLI (python3 -m physics), Examples (physics CLI usage), `validate(scene, placements)`: apply placements (if given), then…, _unknown_placement_ids(), validate(), TestValidate

### Community 44 - "Scan Output Spec"
Cohesion: 0.22
Nodes (9): Item.from_scanned_heightmap (packer3d/models.py), load_scenario (packer3d/scenario.py), Spike iOS App Configuration (project.yml, XcodeGen, ARKit), 2.5D Top-Down Scan Limitation (no undercuts/overhangs), ASCII Map Debug Preview, Scan Coordinate Convention (object-local grid, y=0 table), Heightmap-to-Solid / Voxelization, How the Scan Is Produced (ARKit mesh -> raycast -> flood fill -> min-area rect) (+1 more)

### Community 45 - "Fixture: Object Touching Wall"
Cohesion: 0.39
Nodes (9): Scene: Object Touching Wall (Front-High View), Container (blue packing box), Laptop object (thin pink/red slab, labeled 'laptop'), Shoe object (brown box, labeled 'shoe'), Scene: Object Touching Wall (Overhead-45 View, inferred companion), Blue rectangular container (bin/box), Edge-case test scenario: object-wall contact tolerance, Laptop object (pink, packed near back wall) (+1 more)

### Community 46 - "Label Humanization"
Cohesion: 0.36
Nodes (4): humanize_label(), `labels.get(object_id)` if present, else a humanized id. Humanizing strips a…, Grounding rule 1: naming., TestHumanizeLabel

### Community 47 - "Fixture: Oversized Object"
Cohesion: 0.36
Nodes (8): Container/Bin (blue box), Front-High Camera Angle, oversized_duffel, Oversized Object Edge-Case Test Scenario, Scene: Oversized Object (Front-High View), Scene: Oversized Object (Overhead-45 View), Container/bin (blue box), oversized_duffel (packed object)

### Community 48 - "Fixture: Rotated Object in Corner"
Cohesion: 0.46
Nodes (8): Scene: Rotated Object in Corner (Front-High View), Packing container/bin, shoe (rotated, placed diagonally in corner), toiletry_bag (axis-aligned box), Scene: Rotated Object in Corner (Overhead 45), packing container / bin, shoe (rotated, in corner), toiletry_bag

### Community 49 - "Fixture: Stacked Objects"
Cohesion: 0.43
Nodes (8): scene_stacked_objects_front_high.png (PAN test fixture, front-high camera), Large base container/case object (unlabeled, light blue), headphones_case (stacked object, upper tier), toiletry_bag (stacked object, lower tier, resting on base container), Scene: Stacked Objects (Overhead 45 View), Large container/base box, headphones_case object, toiletries_bag object

### Community 50 - "Fixture: Precarious Balance"
Cohesion: 0.46
Nodes (8): Scene: Precarious Balance (Front-High View), Camera (magenta/purple box), Blue packing container/bin, Toiletry bag (teal box), Scene: Precarious Balance (Overhead 45 view), Camera (purple/magenta box), Large blue container/bin (packing space), Toiletry bag (teal box)

### Community 51 - "Fixture: Soft Item Compression"
Cohesion: 0.39
Nodes (8): Clothes (soft/compressible item), Packing Container, Scene: Soft Item Compression (front-high view), clothes_bag (soft/compressible item, magenta box), Packing container (blue bin/bag outline), toiletry_bag (adjacent item, teal box), Scene: Soft Item Compression (Overhead 45), Toiletry Bag item

### Community 52 - "apply_placements Tests"
Cohesion: 0.39
Nodes (3): apply_placements(), Return a NEW Scene with each placed object's pose replaced. Each placement dict…, TestApplyPlacements

### Community 53 - "ScannedItem Adapter"
Cohesion: 0.33
Nodes (5): _json_default(), object_from_scanned_item(), Any, Adapt a `ScannedItem` JSON dict (centimetres, no pose) into an `Object`. cm ->…, TestScannedItem

### Community 56 - "packer3d Dragon Capsule Renders"
Cohesion: 0.47
Nodes (6): Cargo capsule container (cylindrical bounding volume, box+cylinder items), Center of mass (CoM) lateral offset: 12.7 cm vs target, Naive first-fit packing strategy, Dragon naive packing result (debug render), Utilization metric: 24.4% (22 items packed), Optimized packing result (22 items, 24.4% utilization, 1.8cm CoM lateral offset)

### Community 57 - "packer3d Debug Visualizer"
Cohesion: 0.53
Nodes (5): _box_faces(), _cylinder_surface(), main(), Debug render: python -m packer3d.visualize result.json out.png [naive|optimized], render()

### Community 58 - "PRD Demo & Architecture"
Cohesion: 0.33
Nodes (6): Travel-Track Demo Script, Integration Strategy (mocked scene -> LiDAR -> AR -> refinement), Shared Data Contract (meters, coordinate system, quaternion rotation, IDs), System Architecture Diagram (iPhone -> reconstruction -> packing/physics -> placements -> sim/AR), Team Ownership (Person 1-4), Technical Principles (decouple perception/packing/rendering, preserve metric geometry)

### Community 59 - "Overview Build Plan & Risks"
Cohesion: 0.40
Nodes (5): Build Order (v0-v3), Where This Makes Money (foam layout, field kits, carton selection), Risks Table (infeasible plans, capture friction, deformables), Technical Stack (ARKit, RealityKit, Rust/C++ solver, CloudKit), The Gate (hand-pack utilization validation experiment)

### Community 60 - "Project Scope Docs"
Cohesion: 0.50
Nodes (4): Goal-Driven Execution, Scope by Release (v0-v3), Validation Plan (ten-participant hand-pack experiment), Scope for the Hackathon

### Community 61 - "packer3d Coordinate System"
Cohesion: 0.50
Nodes (4): packer3d Coordinate System (x=length,y=width,z=up; min-corner origin), Item.box / Item.cylinder (packer3d/models.py), Item.from_mesh (packer3d/models.py), Item.from_scan (packer3d/models.py)

### Community 62 - "packer3d Obstacle Model"
Cohesion: 0.50
Nodes (3): Obstacle, A fixed axis-aligned block inside the container (hatch, wheel arch, ...)., test_bad_container_rejected()

### Community 63 - "MVP Team & Contract"
Cohesion: 0.67
Nodes (3): Development Order (phase 1-4), Shared Integration Contract (units, coordinates, placement format), Team Split (Person 1-4 roles)

## Ambiguous Edges - Review These
- `Human Judgment Table (to fill in after real PAN run)` → `Hardening Pass (112 tests, 4 real bugs fixed)`  [AMBIGUOUS]
  packer3d/ALGORITHM.md · relation: conceptually_related_to
- `Dragon naive packing result (debug render)` → `Optimized packing result (22 items, 24.4% utilization, 1.8cm CoM lateral offset)`  [AMBIGUOUS]
  packer3d/examples/dragon_optimized.png · relation: semantically_similar_to
- `Scene: nearly full container (front-high view)` → `Scene: nearly full container (overhead 45deg view, inferred companion fixture)`  [AMBIGUOUS]
  pan/fixtures/scene_nearly_full_front_high.png · relation: semantically_similar_to

## Knowledge Gaps
- **48 isolated node(s):** `Foundation`, `ARKit`, `RealityKit`, `packer3d`, `Core Loop (scan -> reconstruct -> pack -> simulate -> AR)` (+43 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 415 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **8 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `Human Judgment Table (to fill in after real PAN run)` and `Hardening Pass (112 tests, 4 real bugs fixed)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Dragon naive packing result (debug render)` and `Optimized packing result (22 items, 24.4% utilization, 1.8cm CoM lateral offset)`?**
  _Edge tagged AMBIGUOUS (relation: semantically_similar_to) - confidence is low._
- **What is the exact relationship between `Scene: nearly full container (front-high view)` and `Scene: nearly full container (overhead 45deg view, inferred companion fixture)`?**
  _Edge tagged AMBIGUOUS (relation: semantically_similar_to) - confidence is low._
- **Why does `Scene` connect `Scene Metrics & Constraints` to `PAN Rollout Ranking`, `OBB Collision (SAT)`, `PAN Observation Rendering`, `Physics JSON I/O`, `OBB Geometry Core`, `PAN Action-Language Generator`, `Support Contact Checking`, `Scene Round-Trip Tests`, `Physics validate() & CLI Docs`, `Physics Validator Pipeline`, `Constraint Tilt Checks`, `Containment Checking`, `Physics Stress Tests`, `PAN Observation Building`, `apply_placements Tests`, `Physics Test Fixtures`, `Support Convex-Hull Math`, `PAN World-Model Tests`?**
  _High betweenness centrality (0.166) - this node is a cross-community bridge._
- **Why does `obb_from()` connect `Containment Checking` to `PAN Rollout Ranking`, `OBB Collision (SAT)`, `Scene Metrics & Constraints`, `PAN Observation Rendering`, `OBB Geometry Core`, `Scene Round-Trip Tests`, `PAN Region/Orientation Phrasing`, `Physics Stress Tests`, `PAN Observation Building`, `packer3d Input Validation`, `Physics Test Fixtures`, `BoxFit Quaternion Tests`, `Support Convex-Hull Math`, `Compressibility Model`?**
  _High betweenness centrality (0.120) - this node is a cross-community bridge._
- **Why does `Object` connect `Physics Test Fixtures` to `OBB Collision (SAT)`, `Scene Metrics & Constraints`, `PAN Observation Rendering`, `Physics JSON I/O`, `OBB Geometry Core`, `PAN Action-Language Generator`, `Support Contact Checking`, `Scene Round-Trip Tests`, `Physics Validator Pipeline`, `Constraint Tilt Checks`, `Containment Checking`, `Physics Stress Tests`, `ScannedItem Adapter`, `Support Convex-Hull Math`, `Compressibility Model`, `Integration & Physics Docs`?**
  _High betweenness centrality (0.069) - this node is a cross-community bridge._
- **Are the 43 inferred relationships involving `Scene` (e.g. with `describe_action()` and `describe_sequence()`) actually correct?**
  _`Scene` has 43 INFERRED edges - model-reasoned connections that need verification._