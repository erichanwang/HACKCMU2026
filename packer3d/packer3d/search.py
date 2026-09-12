"""Search layer: multi-start greedy + simulated annealing over (sequence, orientation),
followed by exact mass-swap balancing."""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, replace
from typing import Optional

from .balance import balance_masses
from .decoder import NAIVE_PARAMS, DecoderParams, decode
from .models import Container, PackResult, validate_items
from .objective import ObjectiveWeights, build_result, evaluate_state, priority_volume_total


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

    # ---- layer 2a: multi-start greedy ------------------------------------------------
    starts = ([(name, w) for name in _sort_keys() for w in config.com_weight_grid]
              if config.multi_start else [("volume", base.w_com)])
    best: Optional[_Cand] = None
    best_params = base
    for name, w in starts:
        params = replace(base, w_com=w)
        seq = sorted_sequence(items, name)
        st = decode(container, items, seq, None, params)
        val = evaluate(st)
        if best is None or val < best.val - 1e-12:
            orient = [st.chosen_orient.get(items[i].id, 0) for i in range(n)]
            best = _Cand(val, seq, orient, st)
            best_params = params
    multistart_time = time.perf_counter() - t0
    greedy_val = best.val

    # ---- layer 2b: simulated annealing over the genome -------------------------------
    cur = best
    iters = accepted = improvements = 0
    budget_t, budget_i = config.time_budget_s, config.max_iterations
    can_move = n >= 2 or any(k > 1 for k in n_orients)
    while can_move and (budget_t > 0 or budget_i is not None):
        prog_t = (time.perf_counter() - t0) / budget_t if budget_t > 0 else 0.0
        prog_i = (iters / budget_i) if budget_i else (1.0 if budget_i is not None else 0.0)
        prog = max(prog_t, prog_i)
        if prog >= 1.0:
            break
        T = config.t_start * (config.t_end / config.t_start) ** prog
        unpacked_idx = [id_to_idx[u["id"]] for u in cur.state.unpacked]
        seq, orient, move = _mutate(cur.seq, cur.orient, unpacked_idx, n_orients, rng)
        st = decode(container, items, seq, orient, best_params)
        val = evaluate(st)
        delta = val - cur.val
        iters += 1
        if delta <= 0 or rng.random() < math.exp(-delta / max(T, 1e-12)):
            cur = _Cand(val, seq, orient, st)
            accepted += 1
            if val < best.val - 1e-12:
                best = cur
                improvements += 1

    # ---- layer 3: mass-swap balancing ------------------------------------------------
    placements = list(best.state.placements)
    swaps = balance_masses(container, placements) if config.balance else 0
    stats = {
        "multistart_runs": len(starts), "multistart_time_s": multistart_time,
        "greedy_objective": greedy_val, "sa_iterations": iters, "sa_accepted": accepted,
        "sa_improvements": improvements, "search_objective": best.val, "balance_swaps": swaps,
        "time_s": time.perf_counter() - t0, "seed": config.seed,
    }
    return build_result("optimized", container, placements, best.state.unpacked, items, weights, stats)
