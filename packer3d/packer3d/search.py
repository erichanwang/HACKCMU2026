"""Search layer: multi-start greedy + simulated annealing over (sequence, orientation),
followed by exact mass-swap balancing."""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, replace
from typing import Optional

from .balance import balance_masses
from .decoder import NAIVE_PARAMS, REASON_TOO_BIG, DecoderParams, decode
from .models import Container, PackResult, validate_items
from .objective import ObjectiveWeights, build_result, evaluate_state, priority_volume_total

GREEDY_BUDGET_FRACTION = 0.45   # share of time_budget_s the multi-start phase may spend
REPAIR_BUDGET_FRACTION = 0.75   # ... and the cumulative share left after the repair sweep
MAX_ELITES = 6                  # starts kept as restart points for the annealer


@dataclass
class OptimizerConfig:
    time_budget_s: float = 5.0            # wall-clock budget incl. multi-start (0 = iterations only)
    max_iterations: Optional[int] = None  # SA iterations cap (None = time budget only)
    seed: int = 0
    t_start: float = 0.1
    t_end: float = 0.002
    multi_start: bool = True
    balance: bool = True
    com_weight_grid: tuple = (0.0, 0.5, 1.5, 3.0)

    def __post_init__(self):
        from .geometry import is_finite_number
        if not is_finite_number(self.time_budget_s) or self.time_budget_s < 0:
            raise ValueError(f"time_budget_s must be a finite number >= 0, got {self.time_budget_s!r}")
        if self.max_iterations is not None:
            if isinstance(self.max_iterations, bool) or not isinstance(self.max_iterations, int) or self.max_iterations < 0:
                raise ValueError(f"max_iterations must be None or a non-negative int, got {self.max_iterations!r}")
        if not is_finite_number(self.t_start) or self.t_start <= 0:
            raise ValueError(f"t_start must be a finite number > 0, got {self.t_start!r}")
        if not is_finite_number(self.t_end) or self.t_end < 0:
            raise ValueError(f"t_end must be a finite number >= 0, got {self.t_end!r}")
        if not self.com_weight_grid or any(not is_finite_number(w) or w < 0 for w in self.com_weight_grid):
            raise ValueError(f"com_weight_grid must be a non-empty tuple of non-negative numbers, got {self.com_weight_grid!r}")


def _sort_keys():
    return {
        "volume": lambda it: -it.bbox_volume,
        "mass": lambda it: -it.mass,
        "longest_edge": lambda it: -max(it.dims),
        "footprint": lambda it: -(sorted(it.dims)[2] * sorted(it.dims)[1]),
        "thinnest": lambda it: min(it.dims),
        "density": lambda it: -(it.mass / it.bbox_volume),
    }


def sorted_sequence(items, key_name: str) -> list:
    key = _sort_keys()[key_name]
    return sorted(range(len(items)), key=lambda i: (-items[i].priority, key(items[i]), i))


@dataclass
class _Cand:
    val: float
    seq: list
    orient: list
    state: object
    params: DecoderParams


def _mutate(seq, orient, unpacked_idx, n_orients, rng: random.Random):
    seq, orient = list(seq), list(orient)
    n = len(seq)
    reorientable = [i for i in range(n) if n_orients[i] > 1]
    moves, w = [], []
    if n >= 2:
        moves += ["swap", "insert", "reverse"]
        w += [0.35, 0.25, 0.15]
    if reorientable:
        moves.append("reorient")
        w.append(0.25)
    if unpacked_idx and n >= 2:
        moves.append("repair")
        w.append(0.2)
    if not moves:
        return seq, orient, "none"
    move = rng.choices(moves, weights=w)[0]
    if move == "swap":
        i, j = rng.sample(range(n), 2)
        seq[i], seq[j] = seq[j], seq[i]
    elif move == "insert":
        i, j = rng.sample(range(n), 2)
        seq.insert(j, seq.pop(i))
    elif move == "reverse":
        i, j = sorted(rng.sample(range(n), 2))
        seq[i:j + 1] = seq[i:j + 1][::-1]
    elif move == "reorient":
        i = rng.choice(reorientable)
        choices = [k for k in range(n_orients[i]) if k != orient[i]]
        orient[i] = rng.choice(choices)
    elif move == "repair":  # push an item that did not fit towards the front of the sequence
        i = rng.choice(unpacked_idx)
        seq.remove(i)
        seq.insert(rng.randrange(0, max(1, n // 4)), i)
    return seq, orient, move


def pack_naive(container: Container, items) -> PackResult:
    """First-fit baseline: given order, first legal orientation, lowest-then-back-then-left position."""
    items = validate_items(items)
    st = decode(container, items, list(range(len(items))), [0] * len(items), NAIVE_PARAMS)
    return build_result("naive", container, st.placements, st.unpacked, items, ObjectiveWeights(),
                        {"strategy": "first-fit bottom-back-left"})


def pack_optimized(container: Container, items, config: Optional[OptimizerConfig] = None,
                   decoder_params: Optional[DecoderParams] = None,
                   weights: Optional[ObjectiveWeights] = None) -> PackResult:
    items = validate_items(items)
    config = config or OptimizerConfig()
    weights = weights or ObjectiveWeights()
    base = decoder_params or DecoderParams()
    rng = random.Random(config.seed)
    t0 = time.perf_counter()
    n = len(items)
    items_by_id = {it.id: it for it in items}
    total_pv = priority_volume_total(items)
    id_to_idx = {it.id: i for i, it in enumerate(items)}
    n_orients = [len(it.orientations()) for it in items]

    def evaluate(st):
        return evaluate_state(st, items_by_id, total_pv, weights)

    # ---- layer 2a: multi-start greedy, time-capped, keeps the best few as restart points --
    # ``pack_naive``'s exact genome goes first: it is the one arrangement the caller can always
    # get for free, so evaluating it here is what makes pack_optimized unable to lose to it -- on
    # a corpus of mostly-soft garments the input order plus first-fit placement beats every
    # sorted order, and without this start the search never even sees that arrangement.
    starts = [(list(range(n)), [0] * n, NAIVE_PARAMS)]
    if config.multi_start:
        # then one shuffled sweep of every sort key per CoM weight, not a flat shuffle: a
        # truncated budget still sees all six orderings.  The shuffle is seeded, so which starts
        # fit in the budget and how ties between equally good starts break differ per seed.
        grid = list(config.com_weight_grid)
        rng.shuffle(grid)
        for sweep, w in enumerate(grid):
            keys = list(_sort_keys())
            rng.shuffle(keys)
            if sweep == 0:  # largest-first is the strongest single sorted ordering by a wide
                keys.remove("volume")  # margin; never let a short budget shuffle it out of reach
                keys.insert(0, "volume")
            starts += [(sorted_sequence(items, k), None, replace(base, w_com=w)) for k in keys]
    else:
        starts.append((sorted_sequence(items, "volume"), None, base))
    greedy_deadline = t0 + GREEDY_BUDGET_FRACTION * config.time_budget_s
    pool: list[_Cand] = []
    for seq, seed_orient, params in starts:
        st = decode(container, items, seq, seed_orient, params)
        orient = [st.chosen_orient.get(items[i].id, 0) for i in range(n)]
        pool.append(_Cand(evaluate(st), seq, orient, st, params))
        if config.time_budget_s > 0 and time.perf_counter() >= greedy_deadline:
            break
    pool.sort(key=lambda c: c.val)  # stable, so equal-objective starts keep the shuffled order
    best = pool[0]
    multistart_runs = len(pool)
    multistart_time = time.perf_counter() - t0
    greedy_val = best.val

    # ---- layer 2a2: repair sweep -- retry the best start with each item it dropped moved to
    # the front of the sequence.  A greedy order starves the last items of floor space, and one
    # targeted decode per dropped item recovers them far more reliably than a random SA move
    # (whole-sequence decodes cost tens of milliseconds, so a short budget buys very few moves).
    repairs = 0
    for iid in [u["id"] for u in best.state.unpacked if u["reason"] != REASON_TOO_BIG]:
        if config.time_budget_s > 0 and time.perf_counter() - t0 >= REPAIR_BUDGET_FRACTION * config.time_budget_s:
            break
        i = id_to_idx[iid]
        seq = [i] + [j for j in best.seq if j != i]
        st = decode(container, items, seq, None, best.params)
        orient = [st.chosen_orient.get(items[k].id, 0) for k in range(n)]
        pool.append(_Cand(evaluate(st), seq, orient, st, best.params))
        repairs += 1
    pool.sort(key=lambda c: c.val)
    elites = pool[:MAX_ELITES]
    best = elites[0]

    # ---- layer 2b: simulated annealing with restarts from the elite starts ---------------
    cur = best
    iters = accepted = improvements = restarts = 0
    since = 0  # iterations since the last improvement, drives both cooling and restarts
    budget_t, budget_i = config.time_budget_s, config.max_iterations
    stall = max(20, 4 * n)
    can_move = n >= 2 or any(k > 1 for k in n_orients)
    while can_move and (budget_t > 0 or budget_i is not None):
        if budget_t > 0 and time.perf_counter() - t0 >= budget_t:
            break
        if budget_i is not None and iters >= budget_i:
            break
        if since >= stall:  # local optimum: reheat from the next elite, perturbed
            restarts += 1
            e = elites[restarts % len(elites)]
            seq, orient = list(e.seq), list(e.orient)
            for _ in range(1 + restarts % 3):
                seq, orient, _m = _mutate(seq, orient, [], n_orients, rng)
            st = decode(container, items, seq, orient, e.params)
            cur = _Cand(evaluate(st), seq, orient, st, e.params)
            iters += 1
            since = 0
            continue
        T = config.t_start * (config.t_end / config.t_start) ** (since / stall)
        # items that are too big for the container can never be repaired into the sequence
        unpacked_idx = [id_to_idx[u["id"]] for u in cur.state.unpacked if u["reason"] != REASON_TOO_BIG]
        seq, orient, move = _mutate(cur.seq, cur.orient, unpacked_idx, n_orients, rng)
        st = decode(container, items, seq, orient, cur.params)
        val = evaluate(st)
        delta = val - cur.val
        iters += 1
        since += 1
        if delta <= 0 or rng.random() < math.exp(-delta / max(T, 1e-12)):
            cur = _Cand(val, seq, orient, st, cur.params)
            accepted += 1
            if val < best.val - 1e-12:
                best = cur
                improvements += 1
                since = 0

    # ---- layer 3: mass-swap balancing ------------------------------------------------
    placements = list(best.state.placements)
    swaps = balance_masses(container, placements) if config.balance else 0
    stats = {
        "multistart_runs": multistart_runs, "multistart_time_s": multistart_time,
        "greedy_objective": greedy_val, "repair_decodes": repairs, "sa_iterations": iters, "sa_accepted": accepted,
        "sa_improvements": improvements, "sa_restarts": restarts,
        "search_objective": best.val, "balance_swaps": swaps,
        "time_s": time.perf_counter() - t0, "seed": config.seed,
    }
    return build_result("optimized", container, placements, best.state.unpacked, items, weights, stats)
